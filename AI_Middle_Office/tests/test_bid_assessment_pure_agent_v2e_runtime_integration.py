from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from app.agents.bid_assessment_pure.action_runtime import (
    ActionLoopContractRejected,
    InformationRequestAction,
    MainAgentDecisionRequest,
    MainAgentModelActionKind,
    MainAgentModelDecision,
    MainAgentProviderOutcome,
    PlanActionRequest,
    ToolCallBatchAction,
)
from app.agents.bid_assessment_pure.answer_contracts import (
    AnswerDraft,
    GroundingKind,
    GroundingRecord,
    GroundingSnapshot,
    GroundingStatus,
    RuntimeFactBlock,
    SourceBasis,
)
from app.agents.bid_assessment_pure.answer_runtime import GroundingIntegrityGuard
from app.agents.bid_assessment_pure.citation_contracts import (
    CitationAuthoritySnapshot,
)
from app.agents.bid_assessment_pure.citation_runtime import (
    AnswerBlockRenderer,
    CitationProjector,
)
from app.agents.bid_assessment_pure.local_runtime_factory import (
    select_local_main_agent_provider,
)
from app.agents.bid_assessment_pure.deepseek_provider import (
    OfficialDeepSeekChatCodec,
)
from app.agents.bid_assessment_pure.planning import (
    ExecutionMode,
    IntentUnderstanding,
)
from app.agents.bid_assessment_pure.provider_ingress_adapter_v2 import (
    DeterministicProviderJsonIngressAdapter,
)
from app.agents.bid_assessment_pure.provider_ingress_v2 import (
    ProviderBoundaryFailureCode,
    ProviderBoundaryRejected,
    ProviderBoundaryV2Config,
    ProviderIngressNormalizationStep,
)
from app.agents.bid_assessment_pure.provider_decision_v2 import (
    ProviderNextActionDecision,
    ProviderNextActionOutcome,
    ProviderRetrievalRequest,
)
from app.agents.bid_assessment_pure.provider_orchestration_v2 import (
    ProviderDecisionAnswerOrchestratorV2,
    ProviderToolCallsOutcomeV2,
)
from app.agents.bid_assessment_pure.provider_runtime import (
    OpenAICompatibleChatCodec,
    ProviderAdapter,
    ProviderAdapterError,
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderFailure,
    ProviderInvocationRequest,
    ProviderModelResult,
    ProviderOutputKind,
    ProviderRequestRenderer,
    ProviderRuntimeInput,
    ProviderStrictMode,
    ProviderStructuredOutputSpec,
    ProviderToolCallProposal,
    ProviderToolChoice,
    ProviderUsage,
    ProviderWireRequest,
)
from app.agents.bid_assessment_pure.provider_runtime_bridge_v2 import (
    DecisionContextAnswerProjectorV2,
    DecisionContextTerminalProjectorV2,
    ProviderBoundaryV2MainAgentActionProvider,
)
from app.agents.bid_assessment_pure.retrieval_convergence_v2 import (
    RetrievalConvergenceDecisionV2,
    RetrievalConvergenceGateV2,
    RetrievalConvergencePolicyV2,
    RetrievalConvergenceReason,
    semantic_progress_signal_refs_from_tool_batch,
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
from app.agents.bid_assessment_pure.runtime_config import PureAgentFeatureConfig
from app.agents.bid_assessment_pure.slot_validation import (
    SlotCapabilitySnapshot,
    SlotRequestDefinition,
    SlotValidatorRegistry,
)
from app.agents.bid_assessment_pure.state import AgentTaskState, AgentTaskStatus
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


class _QueueAdapter:
    def __init__(self, results: list[Any]) -> None:
        self.capabilities = SimpleNamespace(
            max_response_bytes=2 * 1024 * 1024,
            max_arguments_bytes=16 * 1024,
            max_output_tokens=2_000,
            supports_structured_output=True,
            supports_strict_structured_output=False,
            supports_parallel_tool_calls=False,
            max_tool_calls_per_response=4,
        )
        self.results = list(results)
        self.requests: list[Any] = []

    async def invoke(self, request: Any) -> ProviderModelResult:
        self.requests.append(request)
        result = self.results.pop(0)
        return result(request) if callable(result) else result


class _LotNameInput(BaseModel):
    lot_name: str


class _FixedTokenCounter:
    async def count(self, **_: Any) -> int:
        return 100


class _SequenceResponseTransport:
    def __init__(self, contents: list[Any]) -> None:
        self.contents = list(contents)
        self.requests: list[ProviderWireRequest] = []

    async def invoke(self, request: ProviderWireRequest) -> dict[str, Any]:
        self.requests.append(request)
        content = self.contents.pop(0)
        if isinstance(content, dict):
            content = canonical_json(content)
        return {
            "id": f"response:v2q-{len(self.requests)}",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }


class _FiveToolCallTransport:
    async def invoke(self, request: ProviderWireRequest) -> dict[str, Any]:
        del request
        return {
            "id": "response:v2i-overflow",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"provider-tool-call:v2i-{index}",
                                "type": "function",
                                "function": {
                                    "name": "bid_document_search",
                                    "arguments": canonical_json(
                                        {"query": f"资格条件 {index}"}
                                    ),
                                },
                            }
                            for index in range(5)
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }


class _UnknownToolNameTransport:
    async def invoke(self, request: ProviderWireRequest) -> dict[str, Any]:
        del request
        return {
            "id": "response:v2k-unknown-tool",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "provider-tool-call:v2k-unknown",
                                "type": "function",
                                "function": {
                                    "name": "invented_document_lookup",
                                    "arguments": canonical_json(
                                        {"query": "投标资格"}
                                    ),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }


class _CompatibilityProvider:
    def __init__(self, proposal: MainAgentModelDecision) -> None:
        self.proposal = proposal
        self.calls = 0

    async def decide(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentProviderOutcome:
        del context, registry_snapshot
        self.calls += 1
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "provider_result_ref": "model-result:v2e-compatibility",
            "provider_response_hash": canonical_hash({"v1": self.calls}),
            "provider_receipt_ref": "provider-receipt:v2e-compatibility",
            "proposal": self.proposal.model_dump(mode="json"),
            "concise_basis": self.proposal.concise_basis,
        }
        return MainAgentProviderOutcome(
            **body,
            outcome_hash=canonical_hash(body),
        )


class _ExplodingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, **_: Any) -> MainAgentProviderOutcome:
        self.calls += 1
        raise AssertionError("V1 compatibility provider must not be called")


class _AlwaysSaturatedGate:
    policy = RetrievalConvergencePolicyV2()

    def evaluate(self, context: ContextAssemblyResult):
        evidence_count = sum(
            entry.kind is ContextEntryKind.EVIDENCE_ATOM
            for entry in context.projection_entries
        )
        return RetrievalConvergenceDecisionV2.build(
            saturated=True,
            reason_codes=(
                RetrievalConvergenceReason.TOOL_BATCH_LIMIT_REACHED,
            ),
            tool_batch_count=self.policy.max_tool_batches,
            consecutive_no_novelty_batches=0,
            unique_semantic_signal_count=10,
            evidence_atom_count=evidence_count,
            latest_tool_action_sequence=16,
        )


def _registry() -> RegistrySnapshot:
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
    entry = ToolSnapshotEntry(
        name="bid_document_search",
        definition_hash=canonical_hash({"definition": "bid_document_search"}),
        input_schema_hash=canonical_hash(input_schema),
        output_schema_hash=canonical_hash({"type": "object"}),
        binding_hash=canonical_hash({"kind": "local"}),
        safety_hash=canonical_hash(safety.model_dump(mode="json")),
        execution_kind="local",
        safety=safety,
        model_contract=ModelVisibleToolContract(
            name="bid_document_search",
            description="查询当前授权招标文件中的明确条款。",
            input_schema=input_schema,
        ),
    )
    return RegistrySnapshot(
        snapshot_ref="registry-snapshot:v2e",
        snapshot_hash=canonical_hash({"registry": "v2e"}),
        entries=(entry,),
        visible_tool_names=(entry.name,),
        visible_tools_hash=canonical_hash([entry.name]),
    )


def _registry_with_evidence_read() -> RegistrySnapshot:
    base = _registry()
    input_schema = {
        "type": "object",
        "properties": {
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 16,
            }
        },
        "required": ["evidence_refs"],
        "additionalProperties": False,
    }
    safety = base.entries[0].safety
    evidence_read = ToolSnapshotEntry(
        name="evidence_read",
        definition_hash=canonical_hash({"definition": "evidence_read"}),
        input_schema_hash=canonical_hash(input_schema),
        output_schema_hash=canonical_hash({"type": "object"}),
        binding_hash=canonical_hash({"kind": "local-evidence-read"}),
        safety_hash=canonical_hash(safety.model_dump(mode="json")),
        execution_kind="local",
        safety=safety,
        model_contract=ModelVisibleToolContract(
            name="evidence_read",
            description="将当前 Context 中的候选引用升级为可引用 Evidence Atom。",
            input_schema=input_schema,
        ),
    )
    entries = (*base.entries, evidence_read)
    visible_tool_names = tuple(item.name for item in entries)
    return RegistrySnapshot(
        snapshot_ref="registry-snapshot:v2s",
        snapshot_hash=canonical_hash({"registry": "v2s"}),
        entries=entries,
        visible_tool_names=visible_tool_names,
        visible_tools_hash=canonical_hash(visible_tool_names),
    )


def _entry(
    *,
    entry_ref: str,
    kind: ContextEntryKind,
    lane: ContextLane,
    trust: ContextTrustClass,
    protection: ContextProtectionClass,
    content: str,
    authority_label: str = "v2e-test",
    tool_name: str | None = None,
    protocol_pair_ref: str | None = None,
) -> tuple[ContextIncludedEntry, ContextProjectionEntry]:
    body = {
        "entry_ref": entry_ref,
        "stable_key": f"stable:{entry_ref}",
        "source_ref": f"source:{entry_ref}",
        "source_version_ref": f"source-version:{entry_ref}",
        "lane": lane,
        "kind": kind,
        "representation": ContextRepresentation.EXACT,
        "authority_label": authority_label,
        "protection_class": protection,
        "trust_class": trust,
        "source_content_hash": canonical_hash(content),
        "projection_hash": canonical_hash(content),
        "token_count": 10,
        "tool_name": tool_name,
        "protocol_pair_ref": protocol_pair_ref,
    }
    return (
        ContextIncludedEntry(**body),
        ContextProjectionEntry(
            **body,
            content=content,
            untrusted_data=trust is ContextTrustClass.UNTRUSTED_DATA,
        ),
    )


def _context(*, include_provider_control: bool = False) -> ContextAssemblyResult:
    registry = _registry()
    pairs = [
        _entry(
            entry_ref="evidence:v2e",
            kind=ContextEntryKind.EVIDENCE_ATOM,
            lane=ContextLane.OBSERVATION_GROUNDING,
            trust=ContextTrustClass.UNTRUSTED_DATA,
            protection=ContextProtectionClass.PROTECTED,
            content="招标文件要求投标人满足明确资格条件。",
        )
    ]
    if include_provider_control:
        pairs.extend(
            (
                _entry(
                    entry_ref="output-contract:v2e-v1",
                    kind=ContextEntryKind.OUTPUT_CONTRACT,
                    lane=ContextLane.POLICY_PROTOCOL,
                    trust=ContextTrustClass.TRUSTED_POLICY,
                    protection=ContextProtectionClass.MANDATORY_EXACT,
                    content="V1 output contract",
                ),
                _entry(
                    entry_ref="tool-contract:v2e",
                    kind=ContextEntryKind.TOOL_CONTRACT,
                    lane=ContextLane.TOOL_CONTRACT_ACTIVE_CALLS,
                    trust=ContextTrustClass.TRUSTED_TOOL_CONTRACT,
                    protection=ContextProtectionClass.MANDATORY_EXACT,
                    content="Tool contract",
                    tool_name="bid_document_search",
                ),
                _entry(
                    entry_ref="active-tool-call:v2e",
                    kind=ContextEntryKind.ACTIVE_TOOL_CALL,
                    lane=ContextLane.TOOL_CONTRACT_ACTIVE_CALLS,
                    trust=ContextTrustClass.TRUSTED_RUNTIME,
                    protection=ContextProtectionClass.MANDATORY_EXACT,
                    content="Tool call",
                    tool_name="bid_document_search",
                    protocol_pair_ref="protocol-pair:v2e",
                ),
                _entry(
                    entry_ref="active-tool-result:v2e",
                    kind=ContextEntryKind.ACTIVE_TOOL_RESULT,
                    lane=ContextLane.TOOL_CONTRACT_ACTIVE_CALLS,
                    trust=ContextTrustClass.UNTRUSTED_DATA,
                    protection=ContextProtectionClass.MANDATORY_EXACT,
                    content="Tool result",
                    tool_name="bid_document_search",
                    protocol_pair_ref="protocol-pair:v2e",
                ),
            )
        )
    included = tuple(pair[0] for pair in pairs)
    projections = tuple(pair[1] for pair in pairs)
    snapshot = ContextSnapshot(
        snapshot_ref="context-snapshot:v2e",
        snapshot_sequence=1,
        task_ref="task:v2e",
        state_version=1,
        consumer=ContextConsumer.MAIN_AGENT,
        status=ContextAssemblyStatus.READY,
        request_hash=canonical_hash({"request": "v2e"}),
        policy_snapshot_ref="policy:v2e",
        prompt_template_ref="prompt:v2e",
        model_profile_ref="model-profile:v2e",
        model_profile_hash=canonical_hash({"model": "v2e"}),
        context_profile_ref="context-profile:v2e",
        context_profile_hash=canonical_hash({"context": "v2e"}),
        registry_snapshot_ref=registry.snapshot_ref,
        registry_snapshot_hash=registry.snapshot_hash,
        authorization_snapshot_ref="authorization-snapshot:v2e",
        dependency_refs=tuple(pair[0].source_ref for pair in pairs),
        included_entries=included,
        excluded_entries=(),
        compression_receipts=(),
        included_refs=tuple(item.entry_ref for item in included),
        excluded_refs=(),
        limitation_messages=(),
        estimated_input_tokens=100,
        effective_input_budget=4_000,
        reserved_output_tokens=1_500,
        safety_margin_tokens=200,
        projection_hash=canonical_hash(
            [item.model_dump(mode="json") for item in projections]
        ),
        snapshot_hash=canonical_hash({"snapshot": "v2e"}),
    )
    return ContextAssemblyResult(
        snapshot=snapshot,
        projection_entries=projections,
    )


def _context_for_registry(registry: RegistrySnapshot) -> ContextAssemblyResult:
    base = _context(include_provider_control=True)
    snapshot_body = base.snapshot.model_dump(
        mode="python",
        exclude={"snapshot_hash"},
    )
    snapshot_body.update(
        {
            "registry_snapshot_ref": registry.snapshot_ref,
            "registry_snapshot_hash": registry.snapshot_hash,
        }
    )
    snapshot_hash = canonical_hash(
        {
            "base_snapshot_hash": base.snapshot.snapshot_hash,
            "registry_snapshot_ref": registry.snapshot_ref,
            "registry_snapshot_hash": registry.snapshot_hash,
        }
    )
    return ContextAssemblyResult(
        snapshot=ContextSnapshot(**snapshot_body, snapshot_hash=snapshot_hash),
        projection_entries=base.projection_entries,
    )


def _context_with_answer_visibility_entries() -> ContextAssemblyResult:
    """Expose control/history receipts that must never become factual evidence."""

    base = _context(include_provider_control=True)
    extra_pairs = (
        _entry(
            entry_ref="policy-visible:v2j",
            kind=ContextEntryKind.POLICY,
            lane=ContextLane.POLICY_PROTOCOL,
            trust=ContextTrustClass.TRUSTED_POLICY,
            protection=ContextProtectionClass.MANDATORY_EXACT,
            content="Agent policy visible to the model.",
        ),
        _entry(
            entry_ref="task-visible:v2j",
            kind=ContextEntryKind.TASK_STATE,
            lane=ContextLane.ACTIVE_CONTROL,
            trust=ContextTrustClass.TRUSTED_RUNTIME,
            protection=ContextProtectionClass.PROTECTED,
            content="Current Task state visible to the model.",
        ),
        _entry(
            entry_ref="current-user-visible:v2j",
            kind=ContextEntryKind.CURRENT_USER_MESSAGE,
            lane=ContextLane.ACTIVE_CONTROL,
            trust=ContextTrustClass.UNTRUSTED_DATA,
            protection=ContextProtectionClass.MANDATORY_EXACT,
            content="你好，请介绍一下你自己。",
        ),
        _entry(
            entry_ref="resource-receipt:v2j",
            kind=ContextEntryKind.GROUNDING,
            lane=ContextLane.OBSERVATION_GROUNDING,
            trust=ContextTrustClass.UNTRUSTED_DATA,
            protection=ContextProtectionClass.PROTECTED,
            content="Authorized resource receipt; evidence is not loaded.",
        ),
        _entry(
            entry_ref="limitation-receipt:v2j",
            kind=ContextEntryKind.LIMITATION,
            lane=ContextLane.OBSERVATION_GROUNDING,
            trust=ContextTrustClass.TRUSTED_RUNTIME,
            protection=ContextProtectionClass.PROTECTED,
            content="A persisted limitation receipt.",
        ),
    )
    included = (*base.snapshot.included_entries, *(pair[0] for pair in extra_pairs))
    projections = (*base.projection_entries, *(pair[1] for pair in extra_pairs))
    snapshot_body = base.snapshot.model_dump(mode="python", exclude={"snapshot_hash"})
    snapshot_body.update(
        {
            "dependency_refs": tuple(
                dict.fromkeys(entry.source_ref for entry in included)
            ),
            "included_entries": included,
            "included_refs": tuple(entry.entry_ref for entry in included),
            "estimated_input_tokens": 100 + 10 * len(extra_pairs),
            "projection_hash": canonical_hash(
                [entry.model_dump(mode="json") for entry in projections]
            ),
        }
    )
    return ContextAssemblyResult(
        snapshot=ContextSnapshot(
            **snapshot_body,
            snapshot_hash=canonical_hash(
                {
                    "included_refs": snapshot_body["included_refs"],
                    "projection_hash": snapshot_body["projection_hash"],
                }
            ),
        ),
        projection_entries=projections,
    )


def _context_with_resource_identity_receipt() -> ContextAssemblyResult:
    base = _context(include_provider_control=True)
    identity_pair = _entry(
        entry_ref="resource-identity:v2l-bid",
        kind=ContextEntryKind.GROUNDING,
        lane=ContextLane.OBSERVATION_GROUNDING,
        trust=ContextTrustClass.UNTRUSTED_DATA,
        protection=ContextProtectionClass.PROTECTED,
        authority_label="authorized-resource-identity-receipt",
        content=canonical_json(
            {
                "schema_name": "bid.pure-agent.resource-identity-receipt.v1",
                "resource_kind": "bid_document",
                "display_name": "东莞香港中心项目招标文件",
                "resource_version_ref": "bid-index-version:v2l",
                "authorization_bound": True,
                "claim_scope": "resource_identity_and_load_status_only",
            }
        ),
    )
    included = (*base.snapshot.included_entries, identity_pair[0])
    projections = (*base.projection_entries, identity_pair[1])
    snapshot_body = base.snapshot.model_dump(mode="python", exclude={"snapshot_hash"})
    snapshot_body.update(
        {
            "dependency_refs": tuple(
                dict.fromkeys(entry.source_ref for entry in included)
            ),
            "included_entries": included,
            "included_refs": tuple(entry.entry_ref for entry in included),
            "estimated_input_tokens": 110,
            "projection_hash": canonical_hash(
                [entry.model_dump(mode="json") for entry in projections]
            ),
        }
    )
    return ContextAssemblyResult(
        snapshot=ContextSnapshot(
            **snapshot_body,
            snapshot_hash=canonical_hash(
                {
                    "included_refs": snapshot_body["included_refs"],
                    "projection_hash": snapshot_body["projection_hash"],
                }
            ),
        ),
        projection_entries=projections,
    )


def _request(
    context: ContextAssemblyResult,
    *,
    registry: RegistrySnapshot | None = None,
) -> MainAgentDecisionRequest:
    registry = registry or _registry()
    body = {
        "task_ref": context.snapshot.task_ref,
        "turn_ref": "turn:v2e",
        "decision_action_ref": "action:v2e",
        "decision_sequence": 1,
        "origin_state_version": 1,
        "active_state_version": 1,
        "execution_mode": ExecutionMode.DIRECT,
        "plan_ref": None,
        "context_snapshot_ref": context.snapshot.snapshot_ref,
        "context_snapshot_hash": context.snapshot.snapshot_hash,
        "registry_snapshot_ref": registry.snapshot_ref,
        "registry_snapshot_hash": registry.snapshot_hash,
        "visible_tools_hash": registry.visible_tools_hash,
        "visible_tool_names": registry.visible_tool_names,
        "observation_refs": (),
    }
    digest = canonical_hash(body)
    return MainAgentDecisionRequest(
        **body,
        request_ref=f"agent-decision-request:{digest.removeprefix('sha256:')}",
        request_hash=digest,
    )


def _context_with_tool_signal_batches(
    signal_batches: tuple[tuple[str, ...], ...],
) -> ContextAssemblyResult:
    base = _context(include_provider_control=True)
    included = list(base.snapshot.included_entries)
    projections = list(base.projection_entries)
    for index, signals in enumerate(signal_batches, start=1):
        content = canonical_json(
            {
                "observation": {
                    "kind": "tool_result",
                    "action_sequence": index * 2,
                    "progress_signal_refs": list(signals),
                },
                "artifact_projection": {
                    "projection_kind": "tool_batch_result",
                    "calls": [],
                },
            }
        )
        receipt, projection = _entry(
            entry_ref=f"observation:v2e-{index}",
            kind=ContextEntryKind.OBSERVATION,
            lane=ContextLane.OBSERVATION_GROUNDING,
            trust=ContextTrustClass.UNTRUSTED_DATA,
            protection=ContextProtectionClass.PROTECTED,
            content=content,
        )
        included.append(receipt)
        projections.append(projection)

    snapshot_body = base.snapshot.model_dump(
        mode="python",
        exclude={"snapshot_hash"},
    )
    snapshot_body.update(
        {
            "dependency_refs": tuple(
                dict.fromkeys(item.source_ref for item in included)
            ),
            "included_entries": tuple(included),
            "included_refs": tuple(item.entry_ref for item in included),
            "estimated_input_tokens": 100 + 10 * len(signal_batches),
            "projection_hash": canonical_hash(
                [item.model_dump(mode="json") for item in projections]
            ),
        }
    )
    snapshot_hash = canonical_hash(
        {
            "snapshot_ref": snapshot_body["snapshot_ref"],
            "included_refs": list(snapshot_body["included_refs"]),
            "projection_hash": snapshot_body["projection_hash"],
        }
    )
    return ContextAssemblyResult(
        snapshot=ContextSnapshot(
            **snapshot_body,
            snapshot_hash=snapshot_hash,
        ),
        projection_entries=tuple(projections),
    )


def _context_with_search_candidates(
    context: ContextAssemblyResult,
) -> ContextAssemblyResult:
    content = canonical_json(
        {
            "observation": {
                "kind": "tool_result",
                "action_sequence": 10,
                "progress_signal_refs": ["candidate:evidence-upgrade"],
            },
            "artifact_projection": {
                "projection_kind": "tool_batch_result",
                "calls": [
                    {
                        "call_ref": "tool-call:v2s-search",
                        "tool_name": "enterprise_knowledge_search",
                        "accepted_for_context": True,
                        "result": {
                            "ok": True,
                            "data_projection": {
                                "kind": "search_candidates",
                                "candidate_count": 2,
                                "candidates": [
                                    {
                                        "evidence_ref": "evidence:v2s-candidate-1",
                                        "locator": "enterprise:test#chunk=1",
                                        "citable": False,
                                    },
                                    {
                                        "evidence_ref": "evidence:v2s-candidate-2",
                                        "locator": "bid:test#page=2",
                                        "citable": False,
                                    },
                                ],
                                "truncated": False,
                            },
                        },
                    }
                ],
            },
        }
    )
    receipt, projection = _entry(
        entry_ref="observation:v2s-search-candidates",
        kind=ContextEntryKind.OBSERVATION,
        lane=ContextLane.OBSERVATION_GROUNDING,
        trust=ContextTrustClass.UNTRUSTED_DATA,
        protection=ContextProtectionClass.PROTECTED,
        content=content,
    )
    included = (*context.snapshot.included_entries, receipt)
    projections = (*context.projection_entries, projection)
    snapshot_body = context.snapshot.model_dump(
        mode="python",
        exclude={"snapshot_hash"},
    )
    snapshot_body.update(
        {
            "dependency_refs": tuple(
                dict.fromkeys(item.source_ref for item in included)
            ),
            "included_entries": included,
            "included_refs": tuple(item.entry_ref for item in included),
            "estimated_input_tokens": (
                context.snapshot.estimated_input_tokens or 0
            )
            + receipt.token_count,
            "projection_hash": canonical_hash(
                [item.model_dump(mode="json") for item in projections]
            ),
        }
    )
    snapshot_hash = canonical_hash(
        {
            "snapshot_ref": snapshot_body["snapshot_ref"],
            "included_refs": list(snapshot_body["included_refs"]),
            "projection_hash": snapshot_body["projection_hash"],
        }
    )
    return ContextAssemblyResult(
        snapshot=ContextSnapshot(**snapshot_body, snapshot_hash=snapshot_hash),
        projection_entries=projections,
    )


def _context_with_rejected_answer_guard_feedback(
    context: ContextAssemblyResult,
    *,
    sequence: int = 12,
    suffix: str = "",
) -> ContextAssemblyResult:
    content = canonical_json(
        {
            "observation": {
                "kind": "answer_draft",
                "action_sequence": sequence,
                "status": "rejected",
            },
            "artifact_projection": {
                "projection_kind": "answer_guard_feedback",
                "status": "rejected",
                "accepted": False,
                "required_actions": [
                    "acquire_citable_evidence_for_each_required_source_basis_then_retry"
                ],
                "issues": [
                    {
                        "code": "support_matrix_unsatisfied",
                        "required_action": (
                            "acquire_citable_evidence_for_each_required_source_basis_then_retry"
                        ),
                    }
                ],
            },
        }
    )
    receipt, projection = _entry(
        entry_ref=f"observation:v2e-answer-guard-rejected{suffix}",
        kind=ContextEntryKind.OBSERVATION,
        lane=ContextLane.OBSERVATION_GROUNDING,
        trust=ContextTrustClass.UNTRUSTED_DATA,
        protection=ContextProtectionClass.PROTECTED,
        content=content,
    )
    included = (*context.snapshot.included_entries, receipt)
    projections = (*context.projection_entries, projection)
    snapshot_body = context.snapshot.model_dump(
        mode="python",
        exclude={"snapshot_hash"},
    )
    snapshot_body.update(
        {
            "dependency_refs": tuple(
                dict.fromkeys(item.source_ref for item in included)
            ),
            "included_entries": included,
            "included_refs": tuple(item.entry_ref for item in included),
            "estimated_input_tokens": (
                context.snapshot.estimated_input_tokens or 0
            )
            + receipt.token_count,
            "projection_hash": canonical_hash(
                [item.model_dump(mode="json") for item in projections]
            ),
        }
    )
    snapshot_hash = canonical_hash(
        {
            "snapshot_ref": snapshot_body["snapshot_ref"],
            "included_refs": list(snapshot_body["included_refs"]),
            "projection_hash": snapshot_body["projection_hash"],
        }
    )
    return ContextAssemblyResult(
        snapshot=ContextSnapshot(
            **snapshot_body,
            snapshot_hash=snapshot_hash,
        ),
        projection_entries=projections,
    )


def _result(
    invocation: Any,
    *,
    sequence: int,
    assistant_text: str | None = None,
    proposals: tuple[ProviderToolCallProposal, ...] = (),
) -> ProviderModelResult:
    output_kind = (
        ProviderOutputKind.TOOL_CALLS if proposals else ProviderOutputKind.TEXT
    )
    response_hash = canonical_hash(
        {
            "sequence": sequence,
            "assistant_text": assistant_text,
            "proposals": [item.arguments_hash for item in proposals],
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
        provider_receipt_ref=f"provider-receipt:v2e-{sequence}",
        response_hash=response_hash,
    )


def _text_result(payload: dict[str, Any] | str, sequence: int):
    def build(invocation: Any) -> ProviderModelResult:
        text = payload if isinstance(payload, str) else canonical_json(payload)
        return _result(invocation, sequence=sequence, assistant_text=text)

    return build


def _retrieve_decision(sequence: int = 1):
    return _text_result(
        {
            "action_kind": "retrieve",
            "concise_basis": "需要检索招标资格要求。",
            "information_needs": [],
            "target_source_bases": ["document"],
            "retrieval_request": {
                "information_needs": ["招标文件中的投标资格要求"],
                "requested_tool_names": ["bid_document_search"],
            },
        },
        sequence,
    )


def _tool_result(invocation: Any) -> ProviderModelResult:
    registry = _registry()
    arguments = {"query": "投标资格"}
    raw = canonical_json(arguments)
    proposal = ProviderToolCallProposal(
        model_turn_ref=invocation.call_ref,
        provider_tool_call_id="provider-tool-call:v2e",
        sequence=1,
        task_ref=invocation.task_ref,
        context_snapshot_ref=invocation.context.snapshot.snapshot_ref,
        state_version=invocation.state_version,
        tool_name="bid_document_search",
        raw_arguments_json=raw,
        raw_arguments_hash=canonical_hash(raw),
        arguments=arguments,
        arguments_hash=canonical_hash(arguments),
        registry_snapshot_ref=registry.snapshot_ref,
        registry_snapshot_hash=registry.snapshot_hash,
        visible_tools_hash=registry.visible_tools_hash,
        authorization_snapshot_ref=(
            invocation.context.snapshot.authorization_snapshot_ref
        ),
    )
    return _result(invocation, sequence=1, proposals=(proposal,))


def _evidence_read_tool_result(invocation: Any) -> ProviderModelResult:
    registry = _registry_with_evidence_read()
    arguments = {"evidence_refs": ["evidence:v2s-candidate-1"]}
    raw = canonical_json(arguments)
    proposal = ProviderToolCallProposal(
        model_turn_ref=invocation.call_ref,
        provider_tool_call_id="provider-tool-call:v2s-evidence-read",
        sequence=1,
        task_ref=invocation.task_ref,
        context_snapshot_ref=invocation.context.snapshot.snapshot_ref,
        state_version=invocation.state_version,
        tool_name="evidence_read",
        raw_arguments_json=raw,
        raw_arguments_hash=canonical_hash(raw),
        arguments=arguments,
        arguments_hash=canonical_hash(arguments),
        registry_snapshot_ref=registry.snapshot_ref,
        registry_snapshot_hash=registry.snapshot_hash,
        visible_tools_hash=canonical_hash(["evidence_read"]),
        authorization_snapshot_ref=(
            invocation.context.snapshot.authorization_snapshot_ref
        ),
    )
    return _result(invocation, sequence=3, proposals=(proposal,))


def _tool_overflow_failure(_: Any) -> ProviderModelResult:
    raise ProviderAdapterError(
        ProviderFailure(
            code=ProviderErrorCode.TOOL_CALL_LIMIT_EXCEEDED,
            safe_message="provider returned 6 Tool Calls; limit is 4",
        )
    )


def _tool_name_not_visible_failure(_: Any) -> ProviderModelResult:
    raise ProviderAdapterError(
        ProviderFailure(
            code=ProviderErrorCode.TOOL_NAME_NOT_VISIBLE,
            safe_message=(
                "provider selected a Tool outside the visible Registry"
            ),
        )
    )


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


def _orchestrator(
    adapter: Any,
    *,
    slot_capability_snapshot: SlotCapabilitySnapshot = (
        _TEST_SLOT_CAPABILITY_SNAPSHOT
    ),
) -> ProviderDecisionAnswerOrchestratorV2:
    return ProviderDecisionAnswerOrchestratorV2(
        adapter=adapter,  # type: ignore[arg-type]
        ingress=DeterministicProviderJsonIngressAdapter(
            ProviderBoundaryV2Config(enabled=True)
        ),
        slot_capability_snapshot=slot_capability_snapshot,
    )


def _bridge(adapter: Any, v1: Any):
    return ProviderBoundaryV2MainAgentActionProvider(
        orchestrator=_orchestrator(adapter),
        v1_compatibility_provider=v1,
    )


def _deepseek_codec_adapter(
    context: ContextAssemblyResult,
    contents: list[Any],
    *,
    max_response_bytes: int = 2 * 1024 * 1024,
) -> tuple[ProviderAdapter, _SequenceResponseTransport]:
    transport = _SequenceResponseTransport(contents)
    capabilities = ProviderCapabilities.build(
        capability_ref="provider-capability:v2q",
        provider_ref="provider:deepseek-official-v2q",
        model_ref="model:deepseek-chat-v2q",
        model_profile_ref=context.snapshot.model_profile_ref,
        model_profile_hash=context.snapshot.model_profile_hash,
        codec_ref=OfficialDeepSeekChatCodec.codec_ref,
        token_counter_ref="provider-token-counter:v2q",
        enabled=True,
        supports_structured_output=True,
        supports_strict_structured_output=False,
        max_response_bytes=max_response_bytes,
        max_output_tokens=2_000,
    )
    return (
        ProviderAdapter(
            capabilities=capabilities,
            codec=OfficialDeepSeekChatCodec(),
            token_counter=_FixedTokenCounter(),  # type: ignore[arg-type]
            transport=transport,  # type: ignore[arg-type]
        ),
        transport,
    )


def _plan_decision() -> MainAgentModelDecision:
    return MainAgentModelDecision(
        action_kind=MainAgentModelActionKind.PLAN,
        concise_basis="问题需要有限滚动规划。",
        plan_request=PlanActionRequest(
            understanding=IntentUnderstanding(
                goal_summary="核验投标资格",
                information_needs=("招标资格", "企业资质"),
                source_hints=("bid_documents", "enterprise_knowledge"),
                clarification_needed=False,
                blocking_slot_name=None,
                execution_mode=ExecutionMode.PLANNED,
                rationale="需要跨来源比较。",
            ),
            reason="需要跨来源比较。",
        ),
    )


def _slot_decision() -> MainAgentModelDecision:
    return MainAgentModelDecision(
        action_kind=MainAgentModelActionKind.REQUEST_INFORMATION,
        concise_basis="缺少用户可补充的标段信息。",
        information_request=InformationRequestAction(
            slot_name="lot_name",
            request_message="请提供需要研判的标段名称。",
            input_model_ref="input-model:lot-name-v1",
            blocking_reason="无法确定适用标段。",
        ),
    )


def _slot_provider_payload() -> dict[str, str]:
    return {
        "slot_kind": "lot_name",
        "request_message": "请提供需要研判的标段名称。",
        "blocking_reason": "无法确定适用标段。",
    }


def test_v2r_slot_registry_freezes_only_executable_definitions() -> None:
    registry = SlotValidatorRegistry()
    with pytest.raises(ValueError, match="input model is not registered"):
        registry.register_slot_definition(
            SlotRequestDefinition(
                slot_kind="lot_name",
                slot_name="lot_name",
                description="目标标段名称，由用户提供。",
                input_model_ref="input-model:lot-name-v1",
            )
        )

    registry.register_input_model(
        "input-model:lot-name-v1",
        _LotNameInput,
        format_guidance="请输入非空标段名称。",
    )
    registry.register_slot_definition(
        SlotRequestDefinition(
            slot_kind="lot_name",
            slot_name="lot_name",
            description="目标标段名称，由用户提供。",
            input_model_ref="input-model:lot-name-v1",
        )
    )

    snapshot = registry.freeze_capability_snapshot()
    assert tuple(item.slot_kind for item in snapshot.definitions) == (
        "lot_name",
    )
    assert snapshot.model_visible_capabilities()[0].model_dump(mode="json") == {
        "slot_kind": "lot_name",
        "description": "目标标段名称，由用户提供。",
    }


def test_v2e_feature_flag_defaults_to_v1_and_explicitly_selects_v2() -> None:
    v1 = _ExplodingProvider()
    default_config = PureAgentFeatureConfig.from_application_settings(
        SimpleNamespace(
            feature_bid_assessment_pure_agent=True,
            feature_bid_assessment_pure_agent_runtime=True,
        )
    )
    assert default_config.provider_boundary_mode == "v1"
    assert (
        select_local_main_agent_provider(
            provider_adapter=ProviderAdapter(),
            v1_provider=v1,
        )
        is v1
    )

    explicit_config = PureAgentFeatureConfig.from_application_settings(
        SimpleNamespace(
            feature_bid_assessment_pure_agent=True,
            feature_bid_assessment_pure_agent_runtime=True,
            feature_bid_assessment_pure_agent_provider_boundary_v2=True,
        )
    )
    selected = select_local_main_agent_provider(
        provider_adapter=ProviderAdapter(),
        v1_provider=v1,
        provider_boundary_v2_enabled=(
            explicit_config.provider_boundary_v2_enabled
        ),
    )
    assert explicit_config.provider_boundary_mode == "v2"
    assert isinstance(selected, ProviderBoundaryV2MainAgentActionProvider)


def test_v2e_answer_context_projection_removes_v1_and_tool_protocol() -> None:
    projected = DecisionContextAnswerProjectorV2().project(
        _context(include_provider_control=True)
    )
    assert projected.snapshot.registry_snapshot_ref is None
    assert projected.snapshot.registry_snapshot_hash is None
    assert tuple(item.kind for item in projected.projection_entries) == (
        ContextEntryKind.EVIDENCE_ATOM,
    )
    assert projected.snapshot.included_refs == ("evidence:v2e",)
    assert set(projected.snapshot.excluded_refs) == {
        "output-contract:v2e-v1",
        "tool-contract:v2e",
        "active-tool-call:v2e",
        "active-tool-result:v2e",
    }


def test_v2e_tool_progress_signals_are_semantic_and_ignore_call_identity() -> None:
    first = {
        "schema_name": "bid.pure-agent.capability.tool-batch-result.v1",
        "calls": [
            {
                "call_ref": "call:first",
                "tool_name": "bid_document_search",
                "accepted_for_context": True,
                "result": {
                    "ok": True,
                    "data": {
                        "candidates": [
                            {"evidence_ref": "evidence:v2e-a"},
                            {"evidence_ref": "evidence:v2e-b"},
                        ]
                    },
                },
            }
        ],
    }
    second = {
        **first,
        "calls": [{**first["calls"][0], "call_ref": "call:second"}],
    }

    first_signals = semantic_progress_signal_refs_from_tool_batch(first)
    second_signals = semantic_progress_signal_refs_from_tool_batch(second)

    assert len(first_signals) == 2
    assert first_signals == second_signals
    assert all(item.startswith("retrieval-signal:") for item in first_signals)


def test_v2e_convergence_gate_detects_no_novelty_and_absolute_batch_limit() -> None:
    repeated = "retrieval-signal:" + "a" * 64
    no_novelty = RetrievalConvergenceGateV2().evaluate(
        _context_with_tool_signal_batches(
            ((repeated,), (repeated,), (repeated,))
        )
    )
    unique_batches = tuple(
        (f"retrieval-signal:{index:064x}",)
        for index in range(8)
    )
    absolute_limit = RetrievalConvergenceGateV2().evaluate(
        _context_with_tool_signal_batches(unique_batches)
    )

    assert no_novelty.saturated is True
    assert no_novelty.reason_codes == (
        RetrievalConvergenceReason.NO_NOVEL_INFORMATION_STREAK,
    )
    assert no_novelty.consecutive_no_novelty_batches == 2
    assert absolute_limit.saturated is True
    assert RetrievalConvergenceReason.TOOL_BATCH_LIMIT_REACHED in (
        absolute_limit.reason_codes
    )


def test_v2e_terminal_projection_keeps_only_recent_tool_observations() -> None:
    context = _context_with_tool_signal_batches(
        tuple(
            (f"retrieval-signal:{index:064x}",)
            for index in range(8)
        )
    )

    projected = DecisionContextTerminalProjectorV2().project(context)

    assert projected.snapshot.registry_snapshot_ref is None
    assert sum(
        entry.kind is ContextEntryKind.OBSERVATION
        for entry in projected.projection_entries
    ) == 4
    assert projected.snapshot.estimated_input_tokens < (
        context.snapshot.estimated_input_tokens
    )


def test_v2i_provider_adapter_types_tool_call_count_overflow() -> None:
    context = _context()
    registry = _registry()
    capabilities = ProviderCapabilities.build(
        capability_ref="provider-capability:v2i",
        provider_ref="provider:v2i",
        model_ref="model:v2i",
        model_profile_ref=context.snapshot.model_profile_ref,
        model_profile_hash=context.snapshot.model_profile_hash,
        codec_ref=OpenAICompatibleChatCodec.codec_ref,
        token_counter_ref="provider-token-counter:v2i",
        enabled=True,
        supports_function_calling=True,
        max_tool_calls_per_response=4,
        max_output_tokens=2_000,
    )
    adapter = ProviderAdapter(
        capabilities=capabilities,
        codec=OpenAICompatibleChatCodec(),
        token_counter=_FixedTokenCounter(),  # type: ignore[arg-type]
        transport=_FiveToolCallTransport(),  # type: ignore[arg-type]
    )
    invocation = ProviderInvocationRequest(
        call_ref="model-call:v2i-overflow",
        task_ref=context.snapshot.task_ref,
        state_version=context.snapshot.state_version,
        consumer=ContextConsumer.MAIN_AGENT,
        context=context,
        registry_snapshot=registry,
        runtime_input=ProviderRuntimeInput.from_payload(
            input_ref="runtime-input:v2i-overflow",
            input_kind="main_agent_next_action_v2",
            payload={
                "tool_call_constraints": {"max_calls_per_response": 4}
            },
        ),
        tool_choice=ProviderToolChoice.AUTO,
        tool_strict_mode=ProviderStrictMode.PREFERRED,
        max_output_tokens=1_000,
    )

    with pytest.raises(ProviderAdapterError) as captured:
        asyncio.run(adapter.invoke(invocation))

    assert captured.value.failure.code is (
        ProviderErrorCode.TOOL_CALL_LIMIT_EXCEEDED
    )
    assert captured.value.failure.safe_message == (
        "provider returned 5 Tool Calls; limit is 4"
    )


def test_v2i_tool_limit_is_visible_and_overflow_recovers_to_tool_calls() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter([_tool_overflow_failure, _tool_result])

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert isinstance(outcome, ProviderToolCallsOutcomeV2)
    assert len(adapter.requests) == 2
    initial_constraints = adapter.requests[0].runtime_input.payload[
        "tool_call_constraints"
    ]
    assert initial_constraints == {
        "max_calls_per_response": 4,
        "parallel_calls_allowed": False,
        "selection_rule": "highest_value_non_overlapping_calls_then_redecide",
        "overflow_behavior": "regenerate_once_without_silent_truncation",
        "information_need_binding": {
            "required": True,
            "allowed_sources": [
                "current_user_request",
                "accepted_unresolved_context",
            ],
            "speculative_calls_forbidden": True,
            "no_need_behavior": "return_next_action_without_tool_calls",
        },
    }
    assert adapter.requests[1].runtime_input.input_kind == (
        "main_agent_tool_call_overflow_repair_v2"
    )
    assert adapter.requests[1].runtime_input.payload["repair_attempt"] == 1
    assert adapter.requests[1].runtime_input.payload[
        "tool_call_constraints"
    ] == initial_constraints
    assert "at most 4" in adapter.requests[1].runtime_input.payload[
        "instruction"
    ]


def test_v2i_overflow_recovery_can_converge_to_next_answer_selection() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _tool_overflow_failure,
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "现有证据足以回答。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert len(adapter.requests) == 2
    assert adapter.requests[1].runtime_input.input_kind == (
        "main_agent_tool_call_overflow_repair_v2"
    )
    assert isinstance(outcome, ProviderNextActionOutcome)
    assert outcome.decision.action_kind is MainAgentModelActionKind.ANSWER


def test_v2i_repeated_overflow_fails_typed_after_one_recovery() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [_tool_overflow_failure, _tool_overflow_failure]
    )

    with pytest.raises(ProviderAdapterError) as captured:
        asyncio.run(
            _orchestrator(adapter).decide_next_action(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    assert captured.value.failure.code is (
        ProviderErrorCode.TOOL_CALL_LIMIT_EXCEEDED
    )
    assert len(adapter.requests) == 2


def test_v2i_overflow_then_invalid_decision_does_not_stack_repairs() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _tool_overflow_failure,
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "结构仍然错误。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                    "answer_text": "不应出现在控制决策中。",
                },
                2,
            ),
        ]
    )

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _orchestrator(adapter).decide_next_action(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    assert captured.value.failure.code is (
        ProviderBoundaryFailureCode.DECISION_SCHEMA_INVALID
    )
    assert captured.value.failure.repair_attempt == 1
    assert len(adapter.requests) == 2


def test_v2k_provider_adapter_types_non_visible_tool_name() -> None:
    context = _context()
    registry = _registry()
    capabilities = ProviderCapabilities.build(
        capability_ref="provider-capability:v2k",
        provider_ref="provider:v2k",
        model_ref="model:v2k",
        model_profile_ref=context.snapshot.model_profile_ref,
        model_profile_hash=context.snapshot.model_profile_hash,
        codec_ref=OpenAICompatibleChatCodec.codec_ref,
        token_counter_ref="provider-token-counter:v2k",
        enabled=True,
        supports_function_calling=True,
        max_tool_calls_per_response=4,
        max_output_tokens=2_000,
    )
    adapter = ProviderAdapter(
        capabilities=capabilities,
        codec=OpenAICompatibleChatCodec(),
        token_counter=_FixedTokenCounter(),  # type: ignore[arg-type]
        transport=_UnknownToolNameTransport(),  # type: ignore[arg-type]
    )
    invocation = ProviderInvocationRequest(
        call_ref="model-call:v2k-unknown-tool",
        task_ref=context.snapshot.task_ref,
        state_version=context.snapshot.state_version,
        consumer=ContextConsumer.MAIN_AGENT,
        context=context,
        registry_snapshot=registry,
        runtime_input=ProviderRuntimeInput.from_payload(
            input_ref="runtime-input:v2k-unknown-tool",
            input_kind="main_agent_next_action_v2",
            payload={"allowed_tool_names": list(registry.visible_tool_names)},
        ),
        tool_choice=ProviderToolChoice.AUTO,
        tool_strict_mode=ProviderStrictMode.PREFERRED,
        max_output_tokens=1_000,
    )

    with pytest.raises(ProviderAdapterError) as captured:
        asyncio.run(adapter.invoke(invocation))

    assert captured.value.failure.code is (
        ProviderErrorCode.TOOL_NAME_NOT_VISIBLE
    )
    assert captured.value.failure.safe_message == (
        "provider selected a Tool outside the visible Registry"
    )
    assert "invented_document_lookup" not in (
        captured.value.failure.safe_message
    )


def test_v2k_non_visible_tool_recovers_to_no_tool_answer_selection() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _tool_name_not_visible_failure,
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "普通交流不需要调用工具。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert len(adapter.requests) == 2
    recovery_input = adapter.requests[1].runtime_input
    assert recovery_input.input_kind == "main_agent_tool_registry_repair_v2"
    assert recovery_input.payload["repair_attempt"] == 1
    assert recovery_input.payload["repair_reason"] == (
        ProviderErrorCode.TOOL_NAME_NOT_VISIBLE.value
    )
    assert recovery_input.payload["allowed_tool_names"] == [
        "bid_document_search"
    ]
    assert "Do not invent, alias, guess" in recovery_input.payload[
        "instruction"
    ]
    assert isinstance(outcome, ProviderNextActionOutcome)
    assert outcome.decision.action_kind is MainAgentModelActionKind.ANSWER


def test_v2k_non_visible_tool_recovers_to_visible_tool_calls() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [_tool_name_not_visible_failure, _tool_result]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert isinstance(outcome, ProviderToolCallsOutcomeV2)
    assert outcome.proposals[0].tool_name == "bid_document_search"
    assert len(adapter.requests) == 2
    assert adapter.requests[1].runtime_input.input_kind == (
        "main_agent_tool_registry_repair_v2"
    )


def test_v2k_repeated_non_visible_tool_fails_typed_after_one_recovery() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [_tool_name_not_visible_failure, _tool_name_not_visible_failure]
    )

    with pytest.raises(ProviderAdapterError) as captured:
        asyncio.run(
            _orchestrator(adapter).decide_next_action(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    assert captured.value.failure.code is (
        ProviderErrorCode.TOOL_NAME_NOT_VISIBLE
    )
    assert len(adapter.requests) == 2


@pytest.mark.parametrize(
    ("first_failure", "second_failure", "expected_code"),
    [
        (
            _tool_name_not_visible_failure,
            _tool_overflow_failure,
            ProviderErrorCode.TOOL_CALL_LIMIT_EXCEEDED,
        ),
        (
            _tool_overflow_failure,
            _tool_name_not_visible_failure,
            ProviderErrorCode.TOOL_NAME_NOT_VISIBLE,
        ),
    ],
)
def test_v2k_provider_contract_recoveries_never_stack(
    first_failure: Any,
    second_failure: Any,
    expected_code: ProviderErrorCode,
) -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter([first_failure, second_failure])

    with pytest.raises(ProviderAdapterError) as captured:
        asyncio.run(
            _orchestrator(adapter).decide_next_action(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    assert captured.value.failure.code is expected_code
    assert len(adapter.requests) == 2


def test_v2h_next_action_schema_failure_gets_one_bounded_repair_receipt() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "plan",
                    "concise_basis": "需要有限规划。",
                    "information_needs": [],
                    "target_source_bases": ["document", "enterprise"],
                    "answer_text": "不应出现在控制决策中。",
                },
                1,
            ),
            _text_result(
                {
                    "action_kind": "plan",
                    "concise_basis": "需要有限规划。",
                    "information_needs": [],
                    "target_source_bases": ["document", "enterprise"],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert isinstance(outcome, ProviderNextActionOutcome)
    assert outcome.repair_attempt == 1
    assert outcome.repaired_from_response_hash is not None
    assert outcome.repair_validation_issues
    assert "$.answer_text" in {
        issue.path for issue in outcome.repair_validation_issues
    }
    assert len(adapter.requests) == 2
    assert adapter.requests[1].tool_choice.value == "none"
    assert (
        adapter.requests[1].runtime_input.input_kind
        == "main_agent_next_action_repair_v2"
    )
    assert adapter.requests[1].runtime_input.payload["validation_issues"] == [
        issue.model_dump(mode="json")
        for issue in outcome.repair_validation_issues
    ]


def test_v2h_next_action_repair_exhaustion_keeps_safe_diagnostic_codes() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    invalid = {
        "action_kind": "answer",
        "information_needs": [],
        "target_source_bases": ["document"],
        "answer_text": "仍然不是合法的控制决策。",
    }
    adapter = _QueueAdapter(
        [_text_result(invalid, 1), _text_result(invalid, 2)]
    )

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _orchestrator(adapter).decide_next_action(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    failure = captured.value.failure
    assert failure.code is ProviderBoundaryFailureCode.DECISION_SCHEMA_INVALID
    assert failure.repair_attempt == 1
    assert failure.validation_issues
    assert all(
        issue.diagnostic_code.startswith("provider_validation.")
        for issue in failure.validation_issues
    )
    assert all("仍然" not in issue.diagnostic_code for issue in failure.validation_issues)


def test_v2h_saturated_forbidden_control_action_is_repaired_to_terminal_action() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    convergence = _AlwaysSaturatedGate().evaluate(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "plan",
                    "concise_basis": "错误地尝试继续规划。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                1,
            ),
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "检索已经饱和，应作出有界回答。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
            convergence=convergence,
        )
    )

    assert isinstance(outcome, ProviderNextActionOutcome)
    assert outcome.decision.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.repair_attempt == 1
    assert len(outcome.repair_validation_issues) == 1
    assert outcome.repair_validation_issues[0].path == "$.action_kind"
    assert (
        outcome.repair_validation_issues[0].error_type
        == "terminal_action_forbidden"
    )
    assert adapter.requests[1].runtime_input.payload["allowed_action_kinds"] == [
        "answer",
        "request_information",
    ]


def test_v2m_saturated_terminal_invalid_source_hints_are_nonfatal() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    convergence = _AlwaysSaturatedGate().evaluate(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "检索已饱和，应进入受约束回答。",
                    "information_needs": [],
                    "target_source_bases": [
                        "bid_document",
                        "enterprise_profile",
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
            registry_snapshot=_registry(),
            convergence=convergence,
        )
    )

    assert isinstance(outcome, ProviderNextActionOutcome)
    assert outcome.decision.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.decision.target_source_bases == ()
    assert outcome.repair_attempt == 0
    assert len(adapter.requests) == 1
    assert adapter.requests[0].tool_choice is ProviderToolChoice.NONE
    assert (
        ProviderIngressNormalizationStep.ADVISORY_SOURCE_HINTS_FILTERED
        in outcome.ingress_receipt.normalization_steps
    )


def test_v2h_saturated_rejected_answer_goes_directly_to_guard_aware_answer() -> None:
    context = _context_with_rejected_answer_guard_feedback(
        _context_with_tool_signal_batches(
            tuple(
                (f"retrieval-signal:{index:064x}",)
                for index in range(8)
            )
        )
    )
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "fact",
                            "text": "招标文件包含明确资格要求。",
                            "grounding_refs": ["evidence:v2e"],
                        }
                    ],
                },
                1,
            )
        ]
    )
    v1 = _ExplodingProvider()

    outcome = asyncio.run(
        _bridge(adapter, v1).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert len(adapter.requests) == 1
    invocation = adapter.requests[0]
    assert invocation.runtime_input.input_kind == "main_agent_answer_projection_v2"
    assert invocation.tool_choice.value == "none"
    assert invocation.registry_snapshot is None
    assert invocation.runtime_input.payload["request"][
        "authorization_kind"
    ] == "runtime_terminal_convergence"
    assert invocation.runtime_input.payload["request"]["guard_feedback_refs"] == [
        "observation:v2e-answer-guard-rejected"
    ]
    assert isinstance(outcome.proposal, MainAgentModelDecision)
    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert v1.calls == 0


def test_v2h_repeated_terminal_guard_rejection_returns_actionable_fallback() -> None:
    context = _context_with_rejected_answer_guard_feedback(
        _context_with_rejected_answer_guard_feedback(
            _context_with_tool_signal_batches(
                tuple(
                    (f"retrieval-signal:{index:064x}",)
                    for index in range(8)
                )
            )
        ),
        sequence=20,
        suffix="-second",
    )
    request = _request(context)
    adapter = _QueueAdapter([])
    v1 = _ExplodingProvider()

    outcome = asyncio.run(
        _bridge(adapter, v1).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert adapter.requests == []
    assert isinstance(outcome.proposal, MainAgentModelDecision)
    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.proposal.answer is not None
    assert outcome.proposal.answer.draft.blocks[0].block_type == "interaction"
    assert "停止重复检索和生成" in (
        outcome.proposal.answer.draft.blocks[0].text
    )
    assert outcome.provider_result_ref.startswith(
        "runtime-terminal-guard-fallback:"
    )
    assert v1.calls == 0


def test_v2r_repeated_guard_rejection_converges_without_retrieval_saturation() -> None:
    context = _context_with_rejected_answer_guard_feedback(
        _context_with_rejected_answer_guard_feedback(_context()),
        sequence=20,
        suffix="-second-unsaturated",
    )
    request = _request(context)
    adapter = _QueueAdapter([])
    v1 = _ExplodingProvider()

    outcome = asyncio.run(
        _bridge(adapter, v1).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert adapter.requests == []
    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.proposal.answer is not None
    assert outcome.proposal.answer.draft.blocks[0].block_type == "interaction"
    assert outcome.provider_result_ref.startswith(
        "runtime-terminal-guard-fallback:"
    )
    assert v1.calls == 0


def test_v2s_guard_rejection_with_candidates_requires_evidence_upgrade() -> None:
    registry = _registry_with_evidence_read()
    context = _context_with_rejected_answer_guard_feedback(
        _context_with_search_candidates(_context_for_registry(registry))
    )
    request = _request(context, registry=registry)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "错误地再次直接回答。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            ),
            _text_result(
                {
                    "action_kind": "retrieve",
                    "concise_basis": "先把候选片段升级为可引用证据。",
                    "information_needs": [],
                    "target_source_bases": ["document", "enterprise"],
                    "retrieval_request": {
                        "information_needs": ["读取现有候选片段的权威正文"],
                        "requested_tool_names": ["evidence_read"],
                    },
                },
                2,
            ),
            _evidence_read_tool_result,
        ]
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=registry,
        )
    )

    assert isinstance(outcome.proposal, ToolCallBatchAction)
    assert [call.tool_name for call in outcome.proposal.calls] == [
        "evidence_read"
    ]
    assert len(adapter.requests) == 3
    first_payload = adapter.requests[0].runtime_input.payload
    assert first_payload["allowed_action_kinds"] == ["retrieve"]
    assert first_payload["next_action_recovery_constraint"] == {
        "reason_code": "answer_guard_evidence_upgrade",
        "required_action_kind": "retrieve",
        "required_tool_names": ["evidence_read"],
        "candidate_refs": [
            "evidence:v2s-candidate-1",
            "evidence:v2s-candidate-2",
        ],
    }
    repair_payload = adapter.requests[1].runtime_input.payload
    assert repair_payload["validation_issues"] == [
        {
            "path": "$.action_kind",
            "error_type": "guard_recovery_action_required",
        }
    ]
    assert adapter.requests[2].registry_snapshot is registry
    assert adapter.requests[2].tool_name_filter == ("evidence_read",)


def test_v2r_request_information_is_repaired_when_no_slot_is_executable() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "request_information",
                    "concise_basis": "缺少目标标段。",
                    "information_needs": ["请提供目标标段"],
                    "target_source_bases": ["user_assertion"],
                },
                1,
            ),
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "当前没有可执行 Slot，应给出有界说明。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _orchestrator(
            adapter,
            slot_capability_snapshot=SlotCapabilitySnapshot.build(),
        ).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert isinstance(outcome, ProviderNextActionOutcome)
    assert outcome.decision.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.repair_attempt == 1
    assert outcome.repair_validation_issues[0].error_type == (
        "action_not_available"
    )
    assert "request_information" not in adapter.requests[0].runtime_input.payload[
        "allowed_action_kinds"
    ]
    assert adapter.requests[0].runtime_input.payload[
        "available_slot_capabilities"
    ] == []


def test_v2r_locked_slot_kind_is_whitelisted_and_runtime_materialized() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "request_information",
                    "concise_basis": "缺少目标标段。",
                    "information_needs": ["请提供目标标段"],
                    "target_source_bases": ["user_assertion"],
                },
                1,
            ),
            _text_result(
                {
                    "slot_kind": "invented_slot",
                    "request_message": "请补充信息。",
                    "blocking_reason": "缺少信息。",
                },
                2,
            ),
            _text_result(_slot_provider_payload(), 3),
        ]
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert outcome.proposal.action_kind is (
        MainAgentModelActionKind.REQUEST_INFORMATION
    )
    assert outcome.proposal.information_request is not None
    assert outcome.proposal.information_request.slot_name == "lot_name"
    assert outcome.proposal.information_request.input_model_ref == (
        "input-model:lot-name-v1"
    )
    assert len(adapter.requests) == 3
    repair_payload = adapter.requests[2].runtime_input.payload
    assert repair_payload["validation_issues"] == [
        {
            "path": "$.slot_kind",
            "error_type": "slot_kind_not_available",
        }
    ]
    assert "input_model_ref" not in repair_payload["action_payload_schema"][
        "properties"
    ]


def test_v2e_saturated_retrieval_disables_tools_and_forces_terminal_decision() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "现有证据足以作出有界回答。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                1,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "fact",
                            "text": "招标文件包含明确资格要求。",
                            "grounding_refs": ["evidence:v2e"],
                        }
                    ],
                },
                2,
            ),
        ]
    )
    v1 = _ExplodingProvider()
    provider = ProviderBoundaryV2MainAgentActionProvider(
        orchestrator=_orchestrator(adapter),
        v1_compatibility_provider=v1,
        convergence_gate=_AlwaysSaturatedGate(),  # type: ignore[arg-type]
    )

    outcome = asyncio.run(
        provider.decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    terminal_invocation = adapter.requests[0]
    assert terminal_invocation.registry_snapshot is None
    assert terminal_invocation.tool_choice.value == "none"
    assert terminal_invocation.context.snapshot.snapshot_ref.startswith(
        "terminal-decision-context-v2:"
    )
    assert terminal_invocation.runtime_input.payload[
        "retrieval_convergence"
    ]["saturated"] is True
    assert isinstance(outcome.proposal, MainAgentModelDecision)
    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.request_ref == request.request_ref
    assert v1.calls == 0


def test_v2e_answer_branch_uses_two_calls_and_returns_v1_compatible_answer() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "证据足以回答。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                1,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "fact",
                            "text": "招标文件要求投标人满足明确资格条件。",
                            "grounding_refs": ["evidence:v2e"],
                        }
                    ],
                },
                2,
            ),
        ]
    )
    v1 = _ExplodingProvider()

    outcome = asyncio.run(
        _bridge(adapter, v1).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert len(adapter.requests) == 2
    assert adapter.requests[0].registry_snapshot is not None
    assert adapter.requests[1].registry_snapshot is None
    assert adapter.requests[1].context.snapshot.registry_snapshot_ref is None
    assert isinstance(outcome.proposal, MainAgentModelDecision)
    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.proposal.answer is not None
    assert outcome.proposal.answer.draft.context_snapshot_ref == (
        request.context_snapshot_ref
    )
    assert v1.calls == 0


def test_v2j_answer_visibility_is_not_factual_grounding_authority() -> None:
    context = _context_with_answer_visibility_entries()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "这是无需项目事实的普通交流。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "general_advice",
                            "text": "你好，我可以帮助研判招标要求、企业能力和风险。",
                            "grounding_refs": [],
                        }
                    ],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    answer_request = adapter.requests[1].runtime_input.payload["request"]
    assert answer_request["allowed_grounding_refs"] == ["evidence:v2e"]
    assert answer_request["allowed_limitation_refs"] == [
        "evidence:v2e",
        "resource-receipt:v2j",
        "limitation-receipt:v2j",
    ]
    assert "policy-visible:v2j" not in answer_request["allowed_grounding_refs"]
    assert "task-visible:v2j" not in answer_request["allowed_grounding_refs"]
    assert "current-user-visible:v2j" not in answer_request[
        "allowed_grounding_refs"
    ]
    assert outcome.proposal.answer is not None
    assert outcome.proposal.answer.draft.referenced_grounding_refs() == ()


def test_v2l_resource_identity_is_separate_runtime_fact_authority() -> None:
    context = _context_with_resource_identity_receipt()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "Runtime 已持有受控资源身份回执。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "runtime_fact",
                            "text": "当前加载的是东莞香港中心项目招标文件。",
                            "grounding_refs": ["resource-identity:v2l-bid"],
                        }
                    ],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    answer_request = adapter.requests[1].runtime_input.payload["request"]
    assert answer_request["allowed_grounding_refs"] == ["evidence:v2e"]
    assert answer_request["allowed_runtime_fact_refs"] == [
        "resource-identity:v2l-bid"
    ]
    assert "resource-identity:v2l-bid" not in answer_request[
        "allowed_limitation_refs"
    ]
    assert "resource-identity:v2l-bid" not in answer_request[
        "allowed_grounding_refs"
    ]
    assert outcome.proposal.answer is not None
    block = outcome.proposal.answer.draft.blocks[0]
    assert isinstance(block, RuntimeFactBlock)
    assert block.grounding_refs == ("resource-identity:v2l-bid",)


def test_v2l_wrong_grounding_category_gets_one_bounded_semantic_repair() -> None:
    context = _context_with_resource_identity_receipt()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "可以说明当前加载资源。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "fact",
                            "text": "当前加载的是东莞香港中心项目招标文件。",
                            "grounding_refs": ["resource-identity:v2l-bid"],
                        }
                    ],
                },
                2,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "runtime_fact",
                            "text": "当前加载的是东莞香港中心项目招标文件。",
                            "grounding_refs": ["resource-identity:v2l-bid"],
                        }
                    ],
                },
                3,
            ),
        ]
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert len(adapter.requests) == 3
    assert adapter.requests[2].runtime_input.input_kind == (
        "main_agent_answer_grounding_repair_v2"
    )
    assert outcome.proposal.answer is not None
    assert isinstance(outcome.proposal.answer.draft.blocks[0], RuntimeFactBlock)


def test_v2l_runtime_fact_passes_grounding_citation_and_rendering_without_citation() -> None:
    context = _context_with_resource_identity_receipt()
    identity_entry = next(
        entry
        for entry in context.projection_entries
        if entry.entry_ref == "resource-identity:v2l-bid"
    )
    draft = RuntimeFactBlock(
        block_id="answer-v2-item-001",
        text="当前加载的是东莞香港中心项目招标文件。",
        grounding_refs=(identity_entry.entry_ref,),
    )
    answer = AnswerDraft(
        response_language="zh-CN",
        blocks=(draft,),
        context_snapshot_ref=context.snapshot.snapshot_ref,
        state_version=context.snapshot.state_version,
    )
    locator_hash = canonical_hash(
        {
            "receipt_entry_ref": identity_entry.entry_ref,
            "source_ref": identity_entry.source_ref,
        }
    )
    record = GroundingRecord(
        grounding_ref=identity_entry.entry_ref,
        context_entry_ref=identity_entry.entry_ref,
        source_ref=identity_entry.source_ref,
        source_basis=SourceBasis.RUNTIME_RECEIPT,
        grounding_kind=GroundingKind.RESOURCE_IDENTITY_RECEIPT,
        source_scope_ref=identity_entry.source_ref,
        authorization_snapshot_ref=context.snapshot.authorization_snapshot_ref,
        source_version_ref=identity_entry.source_version_ref,
        source_head_version_ref=identity_entry.source_version_ref,
        source_content_hash=identity_entry.source_content_hash,
        source_head_content_hash=identity_entry.source_content_hash,
        locator_hash=locator_hash,
        source_head_locator_hash=locator_hash,
        context_projection_hash=identity_entry.projection_hash,
        status=GroundingStatus.SUPPORTED,
        citable=False,
        citation_projection_ready=False,
    )
    scopes = (context.snapshot.task_ref, identity_entry.source_ref)
    grounding = GroundingSnapshot.build(
        task_ref=context.snapshot.task_ref,
        state_version=context.snapshot.state_version,
        context_snapshot_ref=context.snapshot.snapshot_ref,
        context_snapshot_hash=context.snapshot.snapshot_hash,
        authorization_snapshot_ref=context.snapshot.authorization_snapshot_ref,
        allowed_scope_refs=scopes,
        records=(record,),
    )
    task = AgentTaskState(
        task_id=context.snapshot.task_ref,
        session_id="conversation:v2l",
        state_version=context.snapshot.state_version,
        status=AgentTaskStatus.RUNNING,
        execution_mode="direct",
        goal_ref="goal:v2l",
        plan_ref=None,
        pending_context=None,
        in_flight_action_ref="action:v2l",
        observation_refs=(),
        last_error_ref=None,
    )

    validation = GroundingIntegrityGuard().validate(
        task=task,
        context=context,
        draft=answer,
        grounding_snapshot=grounding,
    )
    authority = CitationAuthoritySnapshot.build(
        task_ref=task.task_id,
        state_version=task.state_version,
        context_snapshot_ref=context.snapshot.snapshot_ref,
        context_snapshot_hash=context.snapshot.snapshot_hash,
        grounding_snapshot_ref=grounding.snapshot_ref,
        authorization_snapshot_ref=context.snapshot.authorization_snapshot_ref,
        allowed_scope_refs=scopes,
        records=(),
    )
    citations = CitationProjector().project(
        task=task,
        context=context,
        draft=answer,
        validation=validation,
        grounding_snapshot=grounding,
        authority_snapshot=authority,
    )
    rendered = AnswerBlockRenderer().render(
        task=task,
        draft=answer,
        validation=validation,
        citation_decision=citations,
    )

    assert validation.accepted is True
    assert validation.statement_support[0].citation_required is False
    assert citations.accepted is True
    assert citations.bundle is not None
    assert citations.bundle.citations == ()
    assert rendered.blocks[0].block_type == "runtime_fact"
    assert rendered.blocks[0].citation_refs == ()

    ordinary_receipt = record.model_copy(
        update={
            "grounding_kind": GroundingKind.SOURCE_AVAILABILITY_RECEIPT,
            "status": GroundingStatus.UNKNOWN,
        }
    )
    ordinary_snapshot = GroundingSnapshot.build(
        task_ref=context.snapshot.task_ref,
        state_version=context.snapshot.state_version,
        context_snapshot_ref=context.snapshot.snapshot_ref,
        context_snapshot_hash=context.snapshot.snapshot_hash,
        authorization_snapshot_ref=context.snapshot.authorization_snapshot_ref,
        allowed_scope_refs=scopes,
        records=(ordinary_receipt,),
    )
    rejected = GroundingIntegrityGuard().validate(
        task=task,
        context=context,
        draft=answer,
        grounding_snapshot=ordinary_snapshot,
    )
    assert rejected.accepted is False


def test_v2j_factual_answer_rejects_visible_control_entry_as_grounding() -> None:
    context = _context_with_answer_visibility_entries()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "可以直接回答。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "fact",
                            "text": "用户消息可作为项目事实。",
                            "grounding_refs": ["current-user-visible:v2j"],
                        }
                    ],
                },
                2,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "fact",
                            "text": "用户消息仍不能作为项目事实。",
                            "grounding_refs": ["current-user-visible:v2j"],
                        }
                    ],
                },
                3,
            ),
        ]
    )

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _bridge(adapter, _ExplodingProvider()).decide(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    assert captured.value.failure.code is (
        ProviderBoundaryFailureCode.ANSWER_GROUNDING_REJECTED
    )
    assert captured.value.failure.repair_attempt == 1
    assert len(adapter.requests) == 3


def test_v2e_answer_repair_exhaustion_fails_closed_without_v1_fallback() -> None:
    context = _context(include_provider_control=True)
    request = _request(context)
    invalid_answer = {
        "response_language": "zh-CN",
        "items": [
            {
                "kind": "inference",
                "text": "当前资格是否满足需要比较。",
                "grounding_refs": ["evidence:v2e"],
            }
        ],
    }
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "证据足以回答。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                1,
            ),
            _text_result(invalid_answer, 2),
            _text_result(invalid_answer, 3),
        ]
    )
    v1 = _ExplodingProvider()

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _bridge(adapter, v1).decide(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    assert captured.value.failure.repair_attempt == 1
    assert len(adapter.requests) == 3
    assert v1.calls == 0


def test_v2o_retrieval_opens_only_the_accepted_minimal_tool_surface() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter([_retrieve_decision(), _tool_result])
    v1 = _ExplodingProvider()

    outcome = asyncio.run(
        _bridge(adapter, v1).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert isinstance(outcome.proposal, ToolCallBatchAction)
    assert outcome.proposal.calls[0].tool_name == "bid_document_search"
    assert len(adapter.requests) == 2
    assert adapter.requests[0].tool_choice is ProviderToolChoice.NONE
    assert adapter.requests[0].registry_snapshot is not None
    assert adapter.requests[0].tool_name_filter is None
    assert adapter.requests[1].tool_choice is ProviderToolChoice.REQUIRED
    assert adapter.requests[1].tool_name_filter == ("bid_document_search",)
    assert adapter.requests[1].runtime_input.input_kind == (
        "main_agent_retrieval_tool_calls_v2"
    )
    assert v1.calls == 0


def test_v2o_greeting_stays_on_zero_tool_control_and_answer_calls() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "普通问候不需要检索。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "general_advice",
                            "text": "你好，我可以协助研判投标机会。",
                            "grounding_refs": [],
                        }
                    ],
                },
                2,
            ),
        ]
    )
    v1 = _ExplodingProvider()

    outcome = asyncio.run(
        _bridge(adapter, v1).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert len(adapter.requests) == 2
    assert all(
        request.tool_choice is ProviderToolChoice.NONE
        for request in adapter.requests
    )
    assert adapter.requests[0].runtime_input.payload["decision_mode"] == (
        "control_only"
    )
    assert adapter.requests[0].registry_snapshot is not None
    assert adapter.requests[1].registry_snapshot is None
    assert v1.calls == 0


def test_v2o_retrieval_recovery_stays_inside_the_accepted_tool_subset() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [_retrieve_decision(), _tool_name_not_visible_failure, _tool_result]
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert isinstance(outcome.proposal, ToolCallBatchAction)
    assert len(adapter.requests) == 3
    assert adapter.requests[1].tool_name_filter == ("bid_document_search",)
    assert adapter.requests[2].tool_name_filter == ("bid_document_search",)
    repair_input = adapter.requests[2].runtime_input
    assert repair_input.input_kind == "main_agent_retrieval_tool_calls_repair_v2"
    assert repair_input.payload["repair_attempt"] == 1
    assert repair_input.payload["repair_reason"] == (
        ProviderErrorCode.TOOL_NAME_NOT_VISIBLE.value
    )


def test_v2o_retrieval_contract_requires_needs_and_exact_canonical_tools() -> None:
    retrieval = ProviderRetrievalRequest(
        information_needs=("招标资格要求",),
        requested_tool_names=("bid_document_search",),
    )
    decision = ProviderNextActionDecision(
        action_kind="retrieve",
        concise_basis="需要补充招标文件证据。",
        information_needs=(),
        target_source_bases=(SourceBasis.DOCUMENT,),
        retrieval_request=retrieval,
    )

    assert decision.retrieval_request == retrieval
    with pytest.raises(ValueError):
        ProviderNextActionDecision(
            action_kind="retrieve",
            concise_basis="缺少检索合同。",
            information_needs=(),
            target_source_bases=(),
        )
    with pytest.raises(ValueError):
        ProviderRetrievalRequest(
            information_needs=("招标资格要求",),
            requested_tool_names=("invented_document_lookup",),
        )


def test_v2o_renderer_hides_registry_tools_then_projects_only_the_filter() -> None:
    context = _context()
    registry = _registry()
    capabilities = ProviderCapabilities.build(
        capability_ref="provider-capability:v2o",
        provider_ref="provider:v2o",
        model_ref="model:v2o",
        model_profile_ref=context.snapshot.model_profile_ref,
        model_profile_hash=context.snapshot.model_profile_hash,
        codec_ref=OpenAICompatibleChatCodec.codec_ref,
        token_counter_ref="provider-token-counter:v2o",
        enabled=True,
        supports_function_calling=True,
        max_tool_calls_per_response=4,
        max_output_tokens=2_000,
    )
    common = {
        "task_ref": context.snapshot.task_ref,
        "state_version": context.snapshot.state_version,
        "consumer": ContextConsumer.MAIN_AGENT,
        "context": context,
        "registry_snapshot": registry,
        "max_output_tokens": 1_000,
    }
    control = ProviderInvocationRequest(
        call_ref="model-call:v2o-control",
        tool_choice=ProviderToolChoice.NONE,
        **common,
    )
    retrieval = ProviderInvocationRequest(
        call_ref="model-call:v2o-retrieval",
        tool_name_filter=("bid_document_search",),
        tool_choice=ProviderToolChoice.REQUIRED,
        **common,
    )
    renderer = ProviderRequestRenderer()

    rendered_control = renderer.render(
        invocation=control,
        capabilities=capabilities,
    )
    rendered_retrieval = renderer.render(
        invocation=retrieval,
        capabilities=capabilities,
    )

    assert rendered_control.tools == ()
    assert tuple(tool.name for tool in rendered_retrieval.tools) == (
        "bid_document_search",
    )


@pytest.mark.parametrize(
    ("next_action", "locked_payload", "expected_kind"),
    [
        (
            {
                "action_kind": "plan",
                "concise_basis": "需要有限规划。",
                "information_needs": [],
                "target_source_bases": ["document", "enterprise"],
            },
            _plan_decision().plan_request.model_dump(mode="json"),
            MainAgentModelActionKind.PLAN,
        ),
        (
            {
                "action_kind": "request_information",
                "concise_basis": "缺少标段信息。",
                "information_needs": ["请提供标段名称"],
                "target_source_bases": ["user_assertion"],
            },
            _slot_provider_payload(),
            MainAgentModelActionKind.REQUEST_INFORMATION,
        ),
        (
            {
                "action_kind": "replan",
                "concise_basis": "新证据要求调整现有计划。",
                "information_needs": [],
                "target_source_bases": ["document"],
            },
            {
                **_plan_decision().plan_request.model_dump(mode="json"),
                "revision_reasons": ["evidence_conflict"],
            },
            MainAgentModelActionKind.REPLAN,
        ),
    ],
)
def test_v2n_plan_and_slot_use_locked_payload_without_v1_decision(
    next_action: dict[str, Any],
    locked_payload: dict[str, Any],
    expected_kind: MainAgentModelActionKind,
) -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [_text_result(next_action, 1), _text_result(locked_payload, 2)]
    )
    v1 = _CompatibilityProvider(_slot_decision())

    outcome = asyncio.run(
        _bridge(adapter, v1).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert isinstance(outcome.proposal, MainAgentModelDecision)
    assert outcome.proposal.action_kind is expected_kind
    assert v1.calls == 0
    assert len(adapter.requests) == 2
    assert adapter.requests[1].tool_choice is ProviderToolChoice.NONE
    assert adapter.requests[1].runtime_input.payload["locked_action_kind"] == (
        expected_kind.value
    )
    assert "action_kind" not in locked_payload
    if expected_kind is MainAgentModelActionKind.REQUEST_INFORMATION:
        assert outcome.proposal.information_request is not None
        assert outcome.proposal.information_request.slot_name == "lot_name"
        assert outcome.proposal.information_request.input_model_ref == (
            "input-model:lot-name-v1"
        )
        assert "input_model_ref" not in locked_payload
        assert adapter.requests[1].runtime_input.payload[
            "available_slot_capabilities"
        ] == [
            {
                "slot_kind": "lot_name",
                "description": "目标标段名称，由用户提供。",
            }
        ]


def test_v2n_locked_payload_cannot_redecide_action_and_repairs_once() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "plan",
                    "concise_basis": "需要有限规划。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                1,
            ),
            _text_result(
                {
                    "action_kind": "request_information",
                    **_slot_decision().information_request.model_dump(mode="json"),
                },
                2,
            ),
            _text_result(
                _plan_decision().plan_request.model_dump(mode="json"),
                3,
            ),
        ]
    )
    v1 = _CompatibilityProvider(_slot_decision())

    outcome = asyncio.run(
        _bridge(adapter, v1).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert outcome.proposal.action_kind is MainAgentModelActionKind.PLAN
    assert outcome.proposal.plan_request is not None
    assert len(adapter.requests) == 3
    assert adapter.requests[2].runtime_input.input_kind == (
        "main_agent_locked_action_payload_repair_v2"
    )
    assert v1.calls == 0


def test_v2n_locked_payload_repair_exhaustion_is_typed() -> None:
    context = _context()
    request = _request(context)
    invalid = {
        "action_kind": "request_information",
        **_slot_decision().information_request.model_dump(mode="json"),
    }
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "plan",
                    "concise_basis": "需要有限规划。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                1,
            ),
            _text_result(invalid, 2),
            _text_result(invalid, 3),
        ]
    )
    v1 = _CompatibilityProvider(_slot_decision())

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _bridge(adapter, v1).decide(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    assert captured.value.failure.code is (
        ProviderBoundaryFailureCode.LOCKED_ACTION_PAYLOAD_INVALID
    )
    assert captured.value.failure.repair_attempt == 1
    assert v1.calls == 0


def test_v2n_tool_decision_contract_requires_information_need_binding() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "普通交流无需工具。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            )
        ]
    )

    asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    binding = adapter.requests[0].runtime_input.payload[
        "tool_call_constraints"
    ]["information_need_binding"]
    assert binding == {
        "required": True,
        "allowed_sources": [
            "current_user_request",
            "accepted_unresolved_context",
        ],
        "speculative_calls_forbidden": True,
        "no_need_behavior": "return_next_action_without_tool_calls",
    }


def test_v2p_non_strict_structured_output_is_enabled_for_control_and_answer() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "普通交流无需工具。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            ),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "general_advice",
                            "text": "累了可以先休息一下。",
                            "grounding_refs": [],
                        }
                    ],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert len(adapter.requests) == 2
    assert all(request.structured_output is not None for request in adapter.requests)
    assert all(
        request.structured_output.strict_mode is ProviderStrictMode.PREFERRED
        for request in adapter.requests
        if request.structured_output is not None
    )


def test_v2p_next_action_json_envelope_recovers_once() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result("我会直接回答。", 1),
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "普通交流无需检索。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
            allow_native_tool_calls=False,
        )
    )

    assert isinstance(outcome, ProviderNextActionOutcome)
    assert outcome.decision.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.repair_attempt == 1
    assert outcome.repair_validation_issues[0].path == "$"
    assert outcome.repair_validation_issues[0].error_type == (
        ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID.value
    )
    assert adapter.requests[1].runtime_input.input_kind == (
        "main_agent_next_action_json_envelope_repair_v2"
    )


def test_v2p_locked_payload_json_envelope_recovers_without_redecision() -> None:
    context = _context()
    request = _request(context)
    registry = _registry()
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "plan",
                    "concise_basis": "需要有限规划。",
                    "information_needs": [],
                    "target_source_bases": ["document"],
                },
                1,
            ),
            _text_result("先做一个计划。", 2),
            _text_result(
                _plan_decision().plan_request.model_dump(mode="json"),
                3,
            ),
        ]
    )

    async def run():
        orchestrator = _orchestrator(adapter)
        selected = await orchestrator.decide_next_action(
            request=request,
            context=context,
            registry_snapshot=registry,
            allow_native_tool_calls=False,
        )
        assert isinstance(selected, ProviderNextActionOutcome)
        return await orchestrator.generate_locked_action_payload(
            request=request,
            selected=selected,
            context=context,
            registry_snapshot=registry,
        )

    outcome = asyncio.run(run())

    assert outcome.action_kind is MainAgentModelActionKind.PLAN
    assert outcome.repair_attempt == 1
    assert outcome.repair_validation_issues[0].error_type == (
        ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID.value
    )
    assert adapter.requests[2].runtime_input.input_kind == (
        "main_agent_locked_action_payload_json_envelope_repair_v2"
    )


def test_v2p_answer_json_envelope_recovers_once_under_same_authority() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_result(
                {
                    "action_kind": "answer",
                    "concise_basis": "普通交流无需检索。",
                    "information_needs": [],
                    "target_source_bases": [],
                },
                1,
            ),
            _text_result("累了就先休息一下。", 2),
            _text_result(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "general_advice",
                            "text": "累了可以先休息一下。",
                            "grounding_refs": [],
                        }
                    ],
                },
                3,
            ),
        ]
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert len(adapter.requests) == 3
    assert adapter.requests[2].runtime_input.input_kind == (
        "main_agent_answer_projection_json_envelope_repair_v2"
    )


def test_v2p_json_envelope_recovery_exhaustion_never_falls_back_to_v1() -> None:
    context = _context()
    request = _request(context)
    adapter = _QueueAdapter(
        [_text_result("not-json", 1), _text_result("still-not-json", 2)]
    )
    v1 = _CompatibilityProvider(_plan_decision())

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _bridge(adapter, v1).decide(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )
    assert captured.value.failure.code is (
        ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID
    )
    assert captured.value.failure.repair_attempt == 1
    assert len(adapter.requests) == 2
    assert v1.calls == 0


def test_v2q_real_codec_types_structured_json_envelope_failure() -> None:
    context = _context()
    adapter, transport = _deepseek_codec_adapter(context, ["not-json"])
    invocation = ProviderInvocationRequest(
        call_ref="model-call:v2q-codec-invalid-json",
        task_ref=context.snapshot.task_ref,
        state_version=context.snapshot.state_version,
        consumer=ContextConsumer.MAIN_AGENT,
        context=context,
        registry_snapshot=_registry(),
        runtime_input=ProviderRuntimeInput.from_payload(
            input_ref="runtime-input:v2q-codec-invalid-json",
            input_kind="main_agent_next_action_v2",
            payload={"decision_mode": "control_only"},
        ),
        structured_output=ProviderStructuredOutputSpec.from_model(
            schema_name="provider_next_action_v2",
            output_model=ProviderNextActionDecision,
            strict_mode=ProviderStrictMode.PREFERRED,
        ),
        tool_choice=ProviderToolChoice.NONE,
        tool_strict_mode=ProviderStrictMode.PREFERRED,
        max_output_tokens=1_000,
    )

    with pytest.raises(ProviderAdapterError) as captured:
        asyncio.run(adapter.invoke(invocation))

    failure = captured.value.failure
    assert failure.code is ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID
    assert failure.retryable
    assert failure.provider_receipt_ref is not None
    assert failure.response_hash is not None
    assert "not-json" not in failure.safe_message
    assert transport.requests[0].payload["response_format"] == {
        "type": "json_object"
    }


def test_v2q_real_codec_next_action_json_recovers_once() -> None:
    context = _context()
    request = _request(context)
    adapter, transport = _deepseek_codec_adapter(
        context,
        [
            "not-json",
            {
                "action_kind": "answer",
                "concise_basis": "普通交流无需检索。",
                "information_needs": [],
                "target_source_bases": [],
            },
        ],
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
            allow_native_tool_calls=False,
        )
    )

    assert isinstance(outcome, ProviderNextActionOutcome)
    assert outcome.decision.action_kind is MainAgentModelActionKind.ANSWER
    assert outcome.repair_attempt == 1
    assert outcome.repaired_from_response_hash is not None
    assert outcome.repair_validation_issues[0].error_type == (
        ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID.value
    )
    assert len(transport.requests) == 2


def test_v2q_real_codec_locked_payload_json_recovers_without_redecision() -> None:
    context = _context()
    request = _request(context)
    adapter, transport = _deepseek_codec_adapter(
        context,
        [
            {
                "action_kind": "plan",
                "concise_basis": "需要有限规划。",
                "information_needs": [],
                "target_source_bases": ["document", "enterprise"],
            },
            "not-json",
            _plan_decision().plan_request.model_dump(mode="json"),
        ],
    )

    async def run():
        orchestrator = _orchestrator(adapter)
        selected = await orchestrator.decide_next_action(
            request=request,
            context=context,
            registry_snapshot=_registry(),
            allow_native_tool_calls=False,
        )
        assert isinstance(selected, ProviderNextActionOutcome)
        return await orchestrator.generate_locked_action_payload(
            request=request,
            selected=selected,
            context=context,
            registry_snapshot=_registry(),
        )

    outcome = asyncio.run(run())

    assert outcome.action_kind is MainAgentModelActionKind.PLAN
    assert outcome.repair_attempt == 1
    assert outcome.repair_validation_issues[0].error_type == (
        ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID.value
    )
    assert len(transport.requests) == 3


def test_v2q_real_codec_answer_json_recovers_under_same_authority() -> None:
    context = _context()
    request = _request(context)
    adapter, transport = _deepseek_codec_adapter(
        context,
        [
            {
                "action_kind": "answer",
                "concise_basis": "普通交流无需检索。",
                "information_needs": [],
                "target_source_bases": [],
            },
            "not-json",
            {
                "response_language": "zh-CN",
                "items": [
                    {
                        "kind": "general_advice",
                        "text": "可以先休息一下，再继续处理工作。",
                        "grounding_refs": [],
                    }
                ],
            },
        ],
    )

    outcome = asyncio.run(
        _bridge(adapter, _ExplodingProvider()).decide(
            request=request,
            context=context,
            registry_snapshot=_registry(),
        )
    )

    assert outcome.proposal.action_kind is MainAgentModelActionKind.ANSWER
    assert len(transport.requests) == 3
    assert transport.requests[2].payload["response_format"] == {
        "type": "json_object"
    }


def test_v2q_real_codec_json_recovery_exhaustion_is_fail_closed() -> None:
    context = _context()
    request = _request(context)
    adapter, transport = _deepseek_codec_adapter(
        context,
        ["not-json", "still-not-json"],
    )
    v1 = _CompatibilityProvider(_plan_decision())

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _bridge(adapter, v1).decide(
                request=request,
                context=context,
                registry_snapshot=_registry(),
            )
        )

    assert captured.value.failure.code is (
        ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID
    )
    assert captured.value.failure.repair_attempt == 1
    assert len(transport.requests) == 2
    assert v1.calls == 0


def test_v2q_real_codec_json_size_limit_is_not_retried() -> None:
    context = _context()
    request = _request(context)
    adapter, transport = _deepseek_codec_adapter(
        context,
        ["x" * 2_000],
        max_response_bytes=1_024,
    )

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _orchestrator(adapter).decide_next_action(
                request=request,
                context=context,
                registry_snapshot=_registry(),
                allow_native_tool_calls=False,
            )
        )

    assert captured.value.failure.code is (
        ProviderBoundaryFailureCode.JSON_SIZE_LIMIT
    )
    assert captured.value.failure.repair_attempt == 0
    assert len(transport.requests) == 1
