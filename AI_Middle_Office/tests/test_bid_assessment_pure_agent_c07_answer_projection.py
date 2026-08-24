from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents.bid_assessment_pure.action_runtime import (
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
    ProviderCapabilities,
    ProviderOutputKind,
    ProviderSchemaProjector,
    ProviderStrictMode,
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
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash, canonical_json


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


def _provider_context() -> ContextAssemblyResult:
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
        registry_snapshot_ref=None,
        registry_snapshot_hash=None,
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


def _provider_request(context: ContextAssemblyResult) -> MainAgentDecisionRequest:
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
        "registry_snapshot_ref": None,
        "registry_snapshot_hash": None,
        "visible_tools_hash": None,
        "visible_tool_names": (),
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
) -> SimpleNamespace:
    return SimpleNamespace(
        output_kind=output_kind,
        assistant_text=assistant_text,
        structured_payload=structured_payload,
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
        return self.results.pop(0)


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
