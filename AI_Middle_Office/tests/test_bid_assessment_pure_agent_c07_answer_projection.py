from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents.bid_assessment_pure.action_runtime import (
    ActionLoopContractRejected,
    MainAgentDecisionRequest,
    MainAgentModelActionKind,
    MainAgentModelDecision,
)
from app.agents.bid_assessment_pure.answer_contracts import (
    AnswerDraft,
    InteractionBlock,
    LimitationBlock,
    NarrativeBlock,
    StatementBlock,
)
from app.agents.bid_assessment_pure.deepseek_provider import (
    DeepSeekMainAgentActionProvider,
)
from app.agents.bid_assessment_pure.planning import ExecutionMode
from app.agents.bid_assessment_pure.provider_answer_projection import (
    ProviderAnswerProjection,
    ProviderDecisionProjection,
    project_provider_decision,
)
from app.agents.bid_assessment_pure.provider_runtime import (
    ProviderAdapterError,
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderFailure,
    ProviderOutputKind,
    ProviderSchemaProjector,
    ProviderStrictMode,
    ProviderToolCallProposal,
    ProviderToolChoice,
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


def _project(payload: dict[str, object]):
    projection = ProviderDecisionProjection.model_validate(
        {
            "action_kind": "answer",
            "concise_basis": "当前 Context 已有回答所需证据。",
            "payload": payload,
        }
    )
    return project_provider_decision(
        projection,
        context_snapshot_ref="context-snapshot:c07-authority",
        state_version=7,
    )


def _provider_context(
    registry_snapshot: RegistrySnapshot | None = None,
) -> ContextAssemblyResult:
    entry_body = {
        "entry_ref": "evidence:c07-deadline",
        "stable_key": "evidence:deadline",
        "source_ref": "document:c07",
        "source_version_ref": "document-version:c07",
        "lane": ContextLane.OBSERVATION_GROUNDING,
        "kind": ContextEntryKind.EVIDENCE_ATOM,
        "representation": ContextRepresentation.EXACT,
        "authority_label": "persisted-evidence-atom",
        "protection_class": ContextProtectionClass.PROTECTED,
        "trust_class": ContextTrustClass.UNTRUSTED_DATA,
        "source_content_hash": canonical_hash("截止时间证据"),
        "projection_hash": canonical_hash("投标截止时间为2026年9月1日09时30分。"),
        "token_count": 20,
        "tool_name": None,
        "protocol_pair_ref": None,
    }
    included = ContextIncludedEntry(**entry_body)
    projection = ContextProjectionEntry(
        **entry_body,
        content="投标截止时间为2026年9月1日09时30分。",
        untrusted_data=True,
    )
    projection_hash = canonical_hash([projection.model_dump(mode="json")])
    snapshot = ContextSnapshot(
        snapshot_ref="context-snapshot:c07-provider",
        snapshot_sequence=7,
        task_ref="task:c07-provider",
        state_version=7,
        consumer=ContextConsumer.MAIN_AGENT,
        status=ContextAssemblyStatus.READY,
        request_hash=canonical_hash({"request": "c07-provider"}),
        policy_snapshot_ref="policy:c07",
        prompt_template_ref="prompt:c07",
        model_profile_ref="model-profile:c07",
        model_profile_hash=canonical_hash({"model": "c07"}),
        context_profile_ref="context-profile:c07",
        context_profile_hash=canonical_hash({"context": "c07"}),
        registry_snapshot_ref=(
            registry_snapshot.snapshot_ref if registry_snapshot else None
        ),
        registry_snapshot_hash=(
            registry_snapshot.snapshot_hash if registry_snapshot else None
        ),
        authorization_snapshot_ref="authorization-snapshot:c07",
        dependency_refs=("document:c07",),
        included_entries=(included,),
        excluded_entries=(),
        compression_receipts=(),
        included_refs=(included.entry_ref,),
        excluded_refs=(),
        limitation_messages=(),
        estimated_input_tokens=100,
        effective_input_budget=4_000,
        reserved_output_tokens=1_000,
        safety_margin_tokens=200,
        projection_hash=projection_hash,
        snapshot_hash=canonical_hash({"snapshot": "c07-provider"}),
    )
    return ContextAssemblyResult(snapshot=snapshot, projection_entries=(projection,))


def _provider_request(
    context: ContextAssemblyResult,
    *,
    visible_tool_names: tuple[str, ...] = (),
) -> MainAgentDecisionRequest:
    body = {
        "task_ref": context.snapshot.task_ref,
        "turn_ref": "turn:c07-provider",
        "decision_action_ref": "action:c07-provider",
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
            canonical_hash(list(visible_tool_names))
            if context.snapshot.registry_snapshot_ref
            else None
        ),
        "visible_tool_names": visible_tool_names,
        "observation_refs": (),
    }
    digest = canonical_hash(body)
    return MainAgentDecisionRequest(
        **body,
        request_ref=f"agent-decision-request:{digest.removeprefix('sha256:')}",
        request_hash=digest,
    )


def _provider_result(
    *,
    output_kind: ProviderOutputKind,
    sequence: int,
    assistant_text: str | None = None,
    structured_payload: dict[str, object] | None = None,
    call_ref: str | None = None,
    tool_call_proposals: tuple[ProviderToolCallProposal, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        output_kind=output_kind,
        assistant_text=assistant_text,
        structured_payload=structured_payload,
        call_ref=call_ref or f"model-call:c07-{sequence}",
        tool_call_proposals=tool_call_proposals,
        result_ref=f"provider-result:c07-{sequence}",
        response_hash=canonical_hash({"response": sequence}),
        provider_receipt_ref=f"provider-receipt:c07-{sequence}",
    )


class _CaptureAdapter:
    def __init__(self, results: list[SimpleNamespace]) -> None:
        self.capabilities = SimpleNamespace(
            max_response_bytes=1024 * 1024,
            max_output_tokens=1_000,
        )
        self.results = list(results)
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result(request)
        return result


def _provider_registry() -> RegistrySnapshot:
    name = "bid_document_search"
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    model_contract = ModelVisibleToolContract(
        name=name,
        description="查询当前已授权招标文件中的相关条款。",
        input_schema=input_schema,
    )
    safety = ToolSafety(
        effect="read_only",
        data_scope="context_bound",
        external_egress=False,
        requires_approval=False,
    )
    entry = ToolSnapshotEntry(
        name=name,
        definition_hash=canonical_hash({"definition": name}),
        input_schema_hash=canonical_hash(input_schema),
        output_schema_hash=canonical_hash({"type": "object"}),
        binding_hash=canonical_hash({"kind": "local", "handler": name}),
        safety_hash=canonical_hash(safety.model_dump(mode="json")),
        execution_kind="local",
        safety=safety,
        model_contract=model_contract,
    )
    return RegistrySnapshot(
        snapshot_ref="registry-snapshot:c07-provider",
        snapshot_hash=canonical_hash({"registry": "c07-provider"}),
        entries=(entry,),
        visible_tool_names=(name,),
        visible_tools_hash=canonical_hash([name]),
    )


def _invalid_tool_arguments_error() -> ProviderAdapterError:
    return ProviderAdapterError(
        ProviderFailure(
            code=ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
            safe_message=(
                "provider codec rejected response: value is not valid JSON"
            ),
        )
    )


def _compact_tool_decision_result(
    request,
    registry: RegistrySnapshot,
) -> SimpleNamespace:
    return _provider_result(
        output_kind=ProviderOutputKind.STRUCTURED,
        sequence=2,
        call_ref=request.call_ref,
        structured_payload={
            "concise_basis": "仍需读取工期相关证据。",
            "calls": [
                {
                    "tool_name": registry.visible_tool_names[0],
                    "arguments": {"query": "工期要求"},
                }
            ],
        },
    )


def test_compact_supported_statement_upgrades_to_canonical_answer() -> None:
    decision = _project(
        {
            "response_language": "zh-CN",
            "blocks": [
                {
                    "block_type": "statement",
                    "block_id": "statement:deadline",
                    "text": "投标截止时间为2026年9月1日09时30分。",
                    "claim_type": "fact",
                    "epistemic_status": "supported",
                    "grounding_refs": ["evidence:deadline"],
                }
            ],
        }
    )

    assert decision.action_kind is MainAgentModelActionKind.ANSWER
    assert decision.answer is not None
    draft = decision.answer.draft
    assert draft.context_snapshot_ref == "context-snapshot:c07-authority"
    assert draft.state_version == 7
    assert draft.schema_name == "bid.answer.draft.v1"
    statement = draft.blocks[0]
    assert isinstance(statement, StatementBlock)
    assert statement.grounding_refs == ("evidence:deadline",)
    assert statement.quote_refs == ()


def test_compact_projection_preserves_free_block_mix_and_limitation_graph() -> None:
    decision = _project(
        {
            "response_language": "zh-CN",
            "blocks": [
                {
                    "block_type": "narrative",
                    "block_id": "narrative:summary",
                    "text": "以下为当前证据范围内的判断。",
                    "presentation_hint": "heading",
                },
                {
                    "block_type": "statement",
                    "block_id": "statement:qualification",
                    "text": "当前无法确认资质要求是否全部满足。",
                    "claim_type": "fact",
                    "epistemic_status": "unknown",
                    "limitation_refs": ["limitation:missing-source"],
                },
                {
                    "block_type": "limitation",
                    "block_id": "limitation:missing-source",
                    "text": "资料中未提供完整资质附件。",
                    "code": "source_not_provided",
                    "grounding_refs": ["receipt:source-availability"],
                    "applies_to_statement_refs": ["statement:qualification"],
                },
                {
                    "block_type": "interaction",
                    "block_id": "interaction:upload",
                    "text": "请补充资质附件后继续核验。",
                    "slot_ref": "slot:qualification-attachment",
                },
            ],
        }
    )

    assert decision.answer is not None
    blocks = decision.answer.draft.blocks
    assert isinstance(blocks[0], NarrativeBlock)
    assert isinstance(blocks[1], StatementBlock)
    assert isinstance(blocks[2], LimitationBlock)
    assert isinstance(blocks[3], InteractionBlock)


def test_statement_projection_requires_claim_and_epistemic_status() -> None:
    with pytest.raises(ValidationError):
        _project(
            {
                "response_language": "zh-CN",
                "blocks": [
                    {
                        "block_type": "statement",
                        "block_id": "statement:invalid",
                        "text": "缺少必要的认识论状态。",
                        "grounding_refs": ["evidence:any"],
                    }
                ],
            }
        )


def test_canonical_answer_validation_remains_final_authority() -> None:
    with pytest.raises(ValidationError, match="non-supported statement"):
        _project(
            {
                "response_language": "zh-CN",
                "blocks": [
                    {
                        "block_type": "statement",
                        "block_id": "statement:unknown-without-limit",
                        "text": "当前无法确认。",
                        "claim_type": "fact",
                        "epistemic_status": "unknown",
                    }
                ],
            }
        )


def test_provider_cannot_override_runtime_lineage() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _project(
            {
                "response_language": "zh-CN",
                "blocks": [
                    {
                        "block_type": "narrative",
                        "block_id": "narrative:attempt",
                        "text": "尝试覆盖 Runtime 绑定。",
                    }
                ],
                "context_snapshot_ref": "context-snapshot:model-written",
                "state_version": 999,
            }
        )


def test_non_answer_payloads_still_upgrade_to_canonical_contracts() -> None:
    plan = project_provider_decision(
        ProviderDecisionProjection(
            action_kind="plan",
            concise_basis="复杂问题需要短计划。",
            payload={
                "understanding": {
                    "goal_summary": "判断投标风险",
                    "information_needs": ["招标条款", "企业能力"],
                    "source_hints": ["bid_documents", "enterprise_knowledge"],
                    "clarification_needed": False,
                    "blocking_slot_name": None,
                    "execution_mode": "planned",
                    "rationale": "需要跨资料核验。",
                },
                "reason": "需要检索并比较多类证据。",
                "revision_reasons": [],
            },
        ),
        context_snapshot_ref="context-snapshot:c07",
        state_version=3,
    )
    information = project_provider_decision(
        ProviderDecisionProjection(
            action_kind="request_information",
            concise_basis="缺少阻断性资料。",
            payload={
                "slot_name": "bid.target_lot",
                "request_message": "请确认需要研判的标段。",
                "input_model_ref": "input-model:target-lot",
                "business_validator_refs": ["validator:lot-exists"],
                "blocking_reason": "不同标段的资格条件不同。",
            },
        ),
        context_snapshot_ref="context-snapshot:c07",
        state_version=3,
    )

    assert plan.plan_request is not None
    assert information.information_request is not None


def test_provider_visible_schemas_are_smaller_than_canonical_contracts() -> None:
    compact_answer = json.dumps(
        ProviderAnswerProjection.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    canonical_answer = json.dumps(
        AnswerDraft.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    compact_decision = json.dumps(
        ProviderDecisionProjection.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    canonical_decision = json.dumps(
        MainAgentModelDecision.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )

    assert len(compact_answer) < len(canonical_answer)
    assert len(compact_decision) < len(canonical_decision) // 2
    assert "maxProperties" not in compact_decision


def test_compact_decision_schema_is_provider_compatible_without_strict_mode() -> None:
    capabilities = ProviderCapabilities.build(
        capability_ref="provider-capability:c07-schema",
        enabled=True,
        provider_ref="provider:c07-schema",
        model_ref="model:c07-schema",
        model_profile_ref="model-profile:c07-schema",
        model_profile_hash=canonical_hash({"model": "c07-schema"}),
        codec_ref="provider-codec:c07-schema",
        token_counter_ref="provider-token-counter:c07-schema",
        supports_function_calling=True,
        supports_structured_output=True,
    )
    projection = ProviderSchemaProjector().project(
        ProviderDecisionProjection.model_json_schema(),
        capabilities=capabilities,
        strict_mode=ProviderStrictMode.PREFERRED,
        strict_supported=False,
    )

    assert projection.provider_compatible
    assert not projection.strict_enabled
    assert "provider_strict_not_supported" in projection.issues


def test_deepseek_initial_no_tool_branch_uses_compact_projection() -> None:
    context = _provider_context()
    request = _provider_request(context)
    compact_payload = {
        "action_kind": "answer",
        "concise_basis": "Evidence Atom 足以回答截止时间。",
        "payload": {
            "response_language": "zh-CN",
            "blocks": [
                {
                    "block_type": "statement",
                    "block_id": "statement:c07-provider",
                    "text": "投标截止时间为2026年9月1日09时30分。",
                    "claim_type": "fact",
                    "epistemic_status": "supported",
                    "grounding_refs": ["evidence:c07-deadline"],
                }
            ],
        },
    }
    adapter = _CaptureAdapter(
        [
            _provider_result(
                output_kind=ProviderOutputKind.TEXT,
                sequence=1,
                assistant_text=canonical_json(compact_payload),
            )
        ]
    )

    outcome = asyncio.run(
        DeepSeekMainAgentActionProvider(adapter).decide(
            request=request,
            context=context,
            registry_snapshot=None,
        )
    )

    assert len(adapter.requests) == 1
    runtime_payload = adapter.requests[0].runtime_input.payload
    assert "decision_projection_schema" in runtime_payload
    assert "decision_json_schema" not in runtime_payload
    assert any(
        "premise_or_trigger" in rule
        for rule in runtime_payload["answer_business_rules"]
    )
    assert any(
        "CitationProjector" in rule and "block.text" in rule
        for rule in runtime_payload["answer_business_rules"]
    )
    answer_schema = json.dumps(
        runtime_payload["action_payload_schemas"]["answer"],
        ensure_ascii=False,
    )
    assert "context_snapshot_ref" not in answer_schema
    assert "state_version" not in answer_schema
    assert "quote_refs" not in answer_schema
    assert "Do not write citation numbers" in answer_schema
    example = runtime_payload["valid_answer_example_when_evidence_is_sufficient"]
    assert set(example) == {"action_kind", "concise_basis", "payload"}
    assert outcome.proposal.answer is not None
    assert (
        outcome.proposal.answer.draft.context_snapshot_ref
        == context.snapshot.snapshot_ref
    )
    assert outcome.proposal.answer.draft.state_version == context.snapshot.state_version


def test_deepseek_repairs_invalid_tool_arguments_once_and_keeps_authority() -> None:
    registry = _provider_registry()
    context = _provider_context(registry)
    request = _provider_request(
        context,
        visible_tool_names=registry.visible_tool_names,
    )
    adapter = _CaptureAdapter(
        [
            _invalid_tool_arguments_error(),
            lambda invocation: _compact_tool_decision_result(invocation, registry),
        ]
    )

    outcome = asyncio.run(
        DeepSeekMainAgentActionProvider(adapter).decide(
            request=request,
            context=context,
            registry_snapshot=registry,
        )
    )

    assert len(adapter.requests) == 2
    initial, repair = adapter.requests
    assert initial.call_ref != repair.call_ref
    assert repair.context.snapshot.snapshot_ref == context.snapshot.snapshot_ref
    assert repair.state_version == request.origin_state_version
    assert repair.registry_snapshot.snapshot_ref == registry.snapshot_ref
    assert repair.registry_snapshot.snapshot_hash == registry.snapshot_hash
    assert repair.tool_choice is ProviderToolChoice.NONE
    assert repair.structured_output is not None
    assert repair.structured_output.schema_name == "provider_tool_decision_repair"
    assert repair.runtime_input.input_kind == (
        "main_agent_tool_decision_contract_repair"
    )
    feedback = repair.runtime_input.payload["function_call_contract_repair"]
    assert feedback["attempt"] == 1
    assert feedback["rejected_call_ref"] == initial.call_ref
    assert feedback["issues"] == [
        {
            "loc": ["tool_calls", "function", "arguments"],
            "type": "invalid_json_object",
        }
    ]
    assert "raw_arguments" not in feedback
    assert set(repair.runtime_input.payload) == {
        "request",
        "function_call_contract_repair",
    }
    assert "decision_projection_schema" not in repair.runtime_input.payload
    assert "action_payload_schemas" not in repair.runtime_input.payload
    assert "answer_business_rules" not in repair.runtime_input.payload
    assert outcome.proposal.action_kind == "tool_call_batch"
    assert outcome.proposal.calls[0].arguments == {"query": "工期要求"}
    assert outcome.proposal.calls[0].model_turn_ref == repair.call_ref
    assert outcome.proposal.calls[0].context_snapshot_ref == (
        context.snapshot.snapshot_ref
    )
    assert outcome.proposal.calls[0].registry_snapshot_hash == registry.snapshot_hash


def test_deepseek_tool_decision_repair_rejects_non_visible_tool() -> None:
    registry = _provider_registry()
    context = _provider_context(registry)
    request = _provider_request(
        context,
        visible_tool_names=registry.visible_tool_names,
    )
    adapter = _CaptureAdapter(
        [
            _invalid_tool_arguments_error(),
            _provider_result(
                output_kind=ProviderOutputKind.STRUCTURED,
                sequence=2,
                structured_payload={
                    "concise_basis": "尝试选择未授权工具。",
                    "calls": [
                        {
                            "tool_name": "enterprise_knowledge_search",
                            "arguments": {"query": "企业资质"},
                        }
                    ],
                },
            ),
        ]
    )

    with pytest.raises(
        ActionLoopContractRejected,
        match="non-visible Tool",
    ):
        asyncio.run(
            DeepSeekMainAgentActionProvider(adapter).decide(
                request=request,
                context=context,
                registry_snapshot=registry,
            )
        )

    assert len(adapter.requests) == 2


def test_deepseek_tool_decision_repair_requires_tool_calls() -> None:
    registry = _provider_registry()
    context = _provider_context(registry)
    request = _provider_request(
        context,
        visible_tool_names=registry.visible_tool_names,
    )
    adapter = _CaptureAdapter(
        [
            _invalid_tool_arguments_error(),
            _provider_result(
                output_kind=ProviderOutputKind.STRUCTURED,
                sequence=2,
                structured_payload={
                    "concise_basis": "错误地返回空工具列表。",
                    "calls": [],
                },
            ),
        ]
    )

    with pytest.raises(
        ActionLoopContractRejected,
        match="failed Runtime validation",
    ):
        asyncio.run(
            DeepSeekMainAgentActionProvider(adapter).decide(
                request=request,
                context=context,
                registry_snapshot=registry,
            )
        )

    assert len(adapter.requests) == 2


def test_deepseek_tool_decision_repair_rejects_non_structured_result() -> None:
    registry = _provider_registry()
    context = _provider_context(registry)
    request = _provider_request(
        context,
        visible_tool_names=registry.visible_tool_names,
    )
    adapter = _CaptureAdapter(
        [
            _invalid_tool_arguments_error(),
            _provider_result(
                output_kind=ProviderOutputKind.TEXT,
                sequence=2,
                assistant_text=canonical_json(
                    {
                        "concise_basis": "错误输出类型。",
                        "calls": [
                            {
                                "tool_name": registry.visible_tool_names[0],
                                "arguments": {"query": "工期要求"},
                            }
                        ],
                    }
                ),
            ),
        ]
    )

    with pytest.raises(
        ActionLoopContractRejected,
        match="no structured projection",
    ):
        asyncio.run(
            DeepSeekMainAgentActionProvider(adapter).decide(
                request=request,
                context=context,
                registry_snapshot=registry,
            )
        )

    assert len(adapter.requests) == 2


def test_deepseek_tool_argument_contract_repair_is_bounded_to_one() -> None:
    registry = _provider_registry()
    context = _provider_context(registry)
    request = _provider_request(
        context,
        visible_tool_names=registry.visible_tool_names,
    )
    adapter = _CaptureAdapter(
        [
            _invalid_tool_arguments_error(),
            _invalid_tool_arguments_error(),
        ]
    )

    with pytest.raises(ProviderAdapterError) as captured:
        asyncio.run(
            DeepSeekMainAgentActionProvider(adapter).decide(
                request=request,
                context=context,
                registry_snapshot=registry,
            )
        )

    assert captured.value.failure.code is (
        ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION
    )
    assert len(adapter.requests) == 2
    assert adapter.requests[0].call_ref != adapter.requests[1].call_ref


@pytest.mark.parametrize(
    ("code", "safe_message"),
    [
        (
            ProviderErrorCode.AUTHENTICATION_FAILED,
            "official DeepSeek authentication was rejected",
        ),
        (
            ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
            "official DeepSeek returned an invalid response",
        ),
    ],
)
def test_deepseek_does_not_repair_non_tool_argument_failures(
    code: ProviderErrorCode,
    safe_message: str,
) -> None:
    registry = _provider_registry()
    context = _provider_context(registry)
    request = _provider_request(
        context,
        visible_tool_names=registry.visible_tool_names,
    )
    adapter = _CaptureAdapter(
        [ProviderAdapterError(ProviderFailure(code=code, safe_message=safe_message))]
    )

    with pytest.raises(ProviderAdapterError):
        asyncio.run(
            DeepSeekMainAgentActionProvider(adapter).decide(
                request=request,
                context=context,
                registry_snapshot=registry,
            )
        )

    assert len(adapter.requests) == 1


def test_deepseek_repair_uses_compact_schema_and_field_feedback() -> None:
    context = _provider_context()
    request = _provider_request(context)
    repaired_payload = {
        "action_kind": "answer",
        "concise_basis": "修复为精简 Answer Projection。",
        "payload": {
            "response_language": "zh-CN",
            "blocks": [
                {
                    "block_type": "statement",
                    "block_id": "statement:c07-repaired",
                    "text": "投标截止时间为2026年9月1日09时30分。",
                    "claim_type": "fact",
                    "epistemic_status": "supported",
                    "grounding_refs": ["evidence:c07-deadline"],
                }
            ],
        },
    }
    adapter = _CaptureAdapter(
        [
            _provider_result(
                output_kind=ProviderOutputKind.TEXT,
                sequence=1,
                assistant_text=canonical_json(
                    {
                        "action_kind": "answer",
                        "concise_basis": "缺少 payload。",
                    }
                ),
            ),
            _provider_result(
                output_kind=ProviderOutputKind.STRUCTURED,
                sequence=2,
                structured_payload=repaired_payload,
            ),
        ]
    )

    outcome = asyncio.run(
        DeepSeekMainAgentActionProvider(adapter).decide(
            request=request,
            context=context,
            registry_snapshot=None,
        )
    )

    assert len(adapter.requests) == 2
    repair_request = adapter.requests[1]
    assert repair_request.structured_output.schema_name == (
        "provider_decision_projection"
    )
    repair_schema = json.dumps(
        repair_request.structured_output.output_schema,
        ensure_ascii=False,
    )
    assert "plan_request" not in repair_schema
    assert "information_request" not in repair_schema
    assert "context_snapshot_ref" not in repair_schema
    feedback = repair_request.runtime_input.payload["runtime_validation_feedback"]
    assert feedback["attempt"] == 1
    assert any(issue["loc"] == ["payload"] for issue in feedback["issues"])
    assert repair_request.runtime_input.payload["answer_business_rules"]
    assert outcome.proposal.answer is not None


def test_deepseek_feedback_adds_only_allowlisted_business_reason_code() -> None:
    with pytest.raises(ValidationError) as captured:
        _project(
            {
                "response_language": "zh-CN",
                "blocks": [
                    {
                        "block_type": "interaction",
                        "block_id": "interaction:c07-invalid",
                        "text": "请补充资料。",
                        "grounding_refs": ["evidence:not-valid-for-interaction"],
                    }
                ],
            }
        )

    feedback = DeepSeekMainAgentActionProvider._validation_feedback(captured.value)
    assert {
        "loc": ["blocks", "0"],
        "type": "value_error",
        "reason_code": "block_type_field_mismatch",
    } in feedback
    assert all(set(issue) <= {"loc", "type", "reason_code"} for issue in feedback)
