from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.bid_assessment_pure.rag_adapters import (
    DisabledRagSource,
    StaticBidDocumentSearchSource,
    StaticDocumentsOutlineSource,
    StaticEnterpriseKnowledgeSearchSource,
    StaticEvidenceReadSource,
    build_fake_handler_registry,
    build_fake_registry,
)
from app.agents.bid_assessment_pure.registry import (
    BID_DOCUMENT_SEARCH,
    DOCUMENTS_OUTLINE,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    EVIDENCE_READ,
    INITIAL_TOOL_NAMES,
)
from app.agents.bid_assessment_pure.runtime import ToolCallRequest
from app.agents.bid_assessment_pure.state import AgentTaskState
from app.agents.bid_assessment_pure.state_machine import create_running_task
from app.agents.bid_assessment_pure.tool_call_ledger import InMemoryToolCallLedger
from app.agents.bid_assessment_pure.tool_executor import CanonicalToolExecutor
from app.agents.bid_assessment_pure.tool_gateway import (
    CanonicalToolGateway,
    ToolVisibilityProjector,
)
from app.agents.bid_assessment_pure.tool_guards import (
    DefaultExecutionGuard,
    StaticEvidenceScopeAuthorization,
)
from app.agents.bid_assessment_pure.tool_runtime import (
    BindingExecutionResult,
    ExecutionDeadline,
    ToolGuardPolicy,
    ToolProvenanceRecord,
    canonical_hash,
)
from app.agents.bid_assessment_pure.tools import (
    DocumentsOutlineOutput,
    EvidenceAtom,
    EvidenceCandidate,
    EvidenceCandidatesOutput,
    EvidenceReadOutput,
    OutlineEntry,
    ToolErrorCode,
    ToolExecutionContext,
)


def _task() -> AgentTaskState:
    values = create_running_task(
        task_id="task:v602",
        session_id="conversation:v602",
        goal_ref="goal:v602",
    ).model_dump(mode="python")
    values.update(state_version=3, in_flight_action_ref="action:v602")
    return AgentTaskState.model_validate(values)


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        user_ref="user:v602",
        tenant_ref="tenant:v602",
        conversation_ref="conversation:v602",
        task_ref="task:v602",
        state_version=3,
        context_snapshot_ref="context:v602",
        authorization_snapshot_ref="authorization:v602",
        authorized_document_refs=("document:bid-1",),
        enterprise_scope_ref="enterprise:scope-1",
    )


def _policy() -> ToolGuardPolicy:
    return ToolGuardPolicy(
        authorization_snapshot_ref="authorization:v602",
        user_ref="user:v602",
        tenant_ref="tenant:v602",
        task_ref="task:v602",
        runtime_enabled=True,
        allowed_tool_names=INITIAL_TOOL_NAMES,
        allow_local=True,
        allow_mcp=False,
        allow_external_egress=False,
        approved_tool_names=(),
    )


def _deadline(*, seconds: float = 5) -> ExecutionDeadline:
    return ExecutionDeadline(
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=seconds)
    )


def _call(
    *,
    snapshot,
    tool_name: str,
    arguments: dict[str, Any],
    sequence: int,
    provider_id: str | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        call_ref=f"call:v602:{sequence}",
        provider_tool_call_id=provider_id or f"provider-call:v602:{sequence}",
        model_turn_ref="model-turn:v602",
        sequence=sequence,
        task_ref="task:v602",
        action_ref="action:v602",
        context_snapshot_ref="context:v602",
        state_version=3,
        tool_name=tool_name,
        arguments=arguments,
        registry_snapshot_ref=snapshot.snapshot_ref,
        registry_snapshot_hash=snapshot.snapshot_hash,
        visible_tools_hash=snapshot.visible_tools_hash,
        authorization_snapshot_ref="authorization:v602",
    )


def _outline_result() -> BindingExecutionResult:
    output = DocumentsOutlineOutput(
        entries=(OutlineEntry(title="资格要求", level=1, locator="page:10-20"),),
        citable=False,
    )
    return BindingExecutionResult(
        structured_content=output,
        provenance=(
            ToolProvenanceRecord(
                output_ref="document:bid-1",
                source_domain="bid_document",
                source_scope_ref="document:bid-1",
                source_version_ref="document-version:1",
                content_hash=canonical_hash(output.model_dump(mode="json")),
                locator="document:outline",
                citable=False,
            ),
        ),
    )


def _bid_search_result() -> BindingExecutionResult:
    candidate = EvidenceCandidate(
        evidence_ref="evidence:bid-1",
        excerpt="投标截止时间为 2026 年 9 月 1 日。",
        locator="page:12",
        citable=False,
    )
    return BindingExecutionResult(
        structured_content=EvidenceCandidatesOutput(candidates=(candidate,)),
        provenance=(
            ToolProvenanceRecord(
                output_ref=candidate.evidence_ref,
                source_domain="bid_document",
                source_scope_ref="document:bid-1",
                source_version_ref="document-version:1",
                content_hash=canonical_hash(candidate.excerpt),
                locator=candidate.locator,
                citable=False,
            ),
        ),
    )


def _enterprise_search_result() -> BindingExecutionResult:
    candidate = EvidenceCandidate(
        evidence_ref="evidence:enterprise-1",
        excerpt="企业持有有效建筑装修装饰工程专业承包资质。",
        locator="profile:qualification:1",
        citable=False,
    )
    return BindingExecutionResult(
        structured_content=EvidenceCandidatesOutput(candidates=(candidate,)),
        provenance=(
            ToolProvenanceRecord(
                output_ref=candidate.evidence_ref,
                source_domain="enterprise_knowledge",
                source_scope_ref="enterprise:scope-1",
                source_version_ref="enterprise-head:1",
                content_hash=canonical_hash(candidate.excerpt),
                locator=candidate.locator,
                citable=False,
            ),
        ),
    )


def _evidence_read_result() -> BindingExecutionResult:
    bid_atom = EvidenceAtom(
        evidence_ref="evidence:bid-1",
        text="投标截止时间为 2026 年 9 月 1 日。",
        locator="page:12",
        citable=True,
    )
    enterprise_atom = EvidenceAtom(
        evidence_ref="evidence:enterprise-1",
        text="企业持有有效建筑装修装饰工程专业承包资质。",
        locator="profile:qualification:1",
        citable=True,
    )
    return BindingExecutionResult(
        structured_content=EvidenceReadOutput(evidence=(bid_atom, enterprise_atom)),
        provenance=(
            ToolProvenanceRecord(
                output_ref=bid_atom.evidence_ref,
                source_domain="bid_document",
                source_scope_ref="document:bid-1",
                source_version_ref="document-version:1",
                content_hash=canonical_hash(bid_atom.text),
                locator=bid_atom.locator,
                citable=True,
            ),
            ToolProvenanceRecord(
                output_ref=enterprise_atom.evidence_ref,
                source_domain="enterprise_knowledge",
                source_scope_ref="enterprise:scope-1",
                source_version_ref="enterprise-head:1",
                content_hash=canonical_hash(enterprise_atom.text),
                locator=enterprise_atom.locator,
                citable=True,
            ),
        ),
        provider_receipt_ref="fixture-receipt:v602",
    )


class CountingOutlineSource(StaticDocumentsOutlineSource):
    def __init__(self, results):
        super().__init__(results)
        self.calls = 0

    async def read_outline(self, *, document_ref, context):
        self.calls += 1
        return await super().read_outline(document_ref=document_ref, context=context)


class ExplodingBidSearchSource:
    async def search_bid_documents(self, *, query, context):
        raise RuntimeError("secret-provider-endpoint-and-token")


def _gateway(
    *,
    outline_source=None,
    bid_source=None,
    enterprise_source=None,
    evidence_source=None,
):
    registry = build_fake_registry()
    handlers = build_fake_handler_registry(
        outline_source=outline_source
        or StaticDocumentsOutlineSource({"document:bid-1": _outline_result()}),
        bid_search_source=bid_source
        or StaticBidDocumentSearchSource({"投标截止时间": _bid_search_result()}),
        enterprise_search_source=enterprise_source
        or StaticEnterpriseKnowledgeSearchSource(
            {"企业资质": _enterprise_search_result()}
        ),
        evidence_read_source=evidence_source
        or StaticEvidenceReadSource(
            {
                (
                    "evidence:bid-1",
                    "evidence:enterprise-1",
                ): _evidence_read_result()
            }
        ),
    )
    ledger = InMemoryToolCallLedger()
    gateway = CanonicalToolGateway(
        registry=registry,
        executor=CanonicalToolExecutor(local_handlers=handlers),
        ledger=ledger,
        execution_guard=DefaultExecutionGuard(
            evidence_authorization=StaticEvidenceScopeAuthorization(
                {
                    "evidence:bid-1": "document:bid-1",
                    "evidence:enterprise-1": "enterprise:scope-1",
                }
            )
        ),
    )
    projection = ToolVisibilityProjector().project(
        registry=registry,
        relevant_names=INITIAL_TOOL_NAMES,
        context=_context(),
        policy=_policy(),
    )
    return gateway, projection.snapshot


def _execute(gateway, *, call, snapshot, deadline=None):
    return asyncio.run(
        gateway.execute(
            call=call,
            task=_task(),
            snapshot=snapshot,
            context=_context(),
            policy=_policy(),
            deadline=deadline or _deadline(),
        )
    )


def test_gateway_executes_outline_once_and_replays_canonical_result() -> None:
    source = CountingOutlineSource({"document:bid-1": _outline_result()})
    gateway, snapshot = _gateway(outline_source=source)
    call = _call(
        snapshot=snapshot,
        tool_name=DOCUMENTS_OUTLINE,
        arguments={"document_ref": "document:bid-1"},
        sequence=1,
    )
    first = _execute(gateway, call=call, snapshot=snapshot)
    assert first.result.ok
    assert first.accepted_for_context
    assert first.tool_message is not None
    assert first.tool_message.name == DOCUMENTS_OUTLINE
    assert first.tool_message.content_hash == canonical_hash(
        first.result.model_dump(mode="json")
    )
    assert source.calls == 1

    replay = _execute(gateway, call=call, snapshot=snapshot)
    assert replay.result.ok
    assert replay.replayed
    assert replay.accepted_for_context
    assert source.calls == 1


def test_search_adapters_keep_bid_and_enterprise_domains_separate() -> None:
    gateway, snapshot = _gateway()
    bid = _execute(
        gateway,
        call=_call(
            snapshot=snapshot,
            tool_name=BID_DOCUMENT_SEARCH,
            arguments={"query": "投标截止时间"},
            sequence=2,
        ),
        snapshot=snapshot,
    )
    assert bid.result.ok
    assert bid.result.data.candidates[0].evidence_ref == "evidence:bid-1"
    assert bid.result.data.candidates[0].citable is False

    enterprise = _execute(
        gateway,
        call=_call(
            snapshot=snapshot,
            tool_name=ENTERPRISE_KNOWLEDGE_SEARCH,
            arguments={"query": "企业资质"},
            sequence=3,
        ),
        snapshot=snapshot,
    )
    assert enterprise.result.ok
    assert (
        enterprise.result.data.candidates[0].evidence_ref
        == "evidence:enterprise-1"
    )
    assert enterprise.result.data.candidates[0].citable is False


def test_evidence_read_requires_authorized_refs_and_returns_only_citable_atoms() -> None:
    gateway, snapshot = _gateway()
    allowed = _execute(
        gateway,
        call=_call(
            snapshot=snapshot,
            tool_name=EVIDENCE_READ,
            arguments={
                "evidence_refs": ["evidence:bid-1", "evidence:enterprise-1"]
            },
            sequence=4,
        ),
        snapshot=snapshot,
    )
    assert allowed.result.ok
    assert all(atom.citable for atom in allowed.result.data.evidence)

    denied = _execute(
        gateway,
        call=_call(
            snapshot=snapshot,
            tool_name=EVIDENCE_READ,
            arguments={"evidence_refs": ["evidence:outside"]},
            sequence=5,
        ),
        snapshot=snapshot,
    )
    assert not denied.result.ok
    assert denied.result.error.code is ToolErrorCode.ACCESS_DENIED
    assert denied.guard_decisions[-1].code == "EVIDENCE_ACCESS_DENIED"


def test_model_cannot_inject_runtime_scope_through_tool_arguments() -> None:
    source = CountingOutlineSource({"document:bid-1": _outline_result()})
    gateway, snapshot = _gateway(outline_source=source)
    outcome = _execute(
        gateway,
        call=_call(
            snapshot=snapshot,
            tool_name=DOCUMENTS_OUTLINE,
            arguments={
                "document_ref": "document:bid-1",
                "tenant_ref": "tenant:attacker",
                "authorization_snapshot_ref": "authorization:attacker",
            },
            sequence=6,
        ),
        snapshot=snapshot,
    )
    assert not outcome.result.ok
    assert outcome.result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert outcome.guard_decisions[-1].code == "INPUT_VALIDATION_FAILED"
    assert source.calls == 0
    assert "attacker" not in outcome.tool_message.content


def test_execution_scope_denial_prevents_adapter_invocation() -> None:
    source = CountingOutlineSource({"document:outside": _outline_result()})
    gateway, snapshot = _gateway(outline_source=source)
    outcome = _execute(
        gateway,
        call=_call(
            snapshot=snapshot,
            tool_name=DOCUMENTS_OUTLINE,
            arguments={"document_ref": "document:outside"},
            sequence=7,
        ),
        snapshot=snapshot,
    )
    assert not outcome.result.ok
    assert outcome.result.error.code is ToolErrorCode.ACCESS_DENIED
    assert outcome.guard_decisions[-1].code == "DOCUMENT_ACCESS_DENIED"
    assert source.calls == 0


def test_gateway_returns_safe_unavailable_internal_and_contract_errors() -> None:
    unavailable_gateway, snapshot = _gateway(bid_source=DisabledRagSource())
    unavailable = _execute(
        unavailable_gateway,
        call=_call(
            snapshot=snapshot,
            tool_name=BID_DOCUMENT_SEARCH,
            arguments={"query": "投标截止时间"},
            sequence=8,
        ),
        snapshot=snapshot,
    )
    assert unavailable.result.error.code is ToolErrorCode.UNAVAILABLE
    assert unavailable.result.error.retryable

    exploding_gateway, exploding_snapshot = _gateway(
        bid_source=ExplodingBidSearchSource()
    )
    internal = _execute(
        exploding_gateway,
        call=_call(
            snapshot=exploding_snapshot,
            tool_name=BID_DOCUMENT_SEARCH,
            arguments={"query": "投标截止时间"},
            sequence=9,
        ),
        snapshot=exploding_snapshot,
    )
    assert internal.result.error.code is ToolErrorCode.INTERNAL_ERROR
    assert "secret-provider-endpoint-and-token" not in internal.tool_message.content

    invalid_candidate = {
        "candidates": [
            {
                "evidence_ref": "evidence:bid-1",
                "excerpt": "原始候选",
                "locator": "page:12",
                "citable": True,
            }
        ]
    }
    malformed_source = StaticBidDocumentSearchSource(
        {
            "投标截止时间": BindingExecutionResult(
                structured_content=invalid_candidate,
                provenance=(),
            )
        }
    )
    malformed_gateway, malformed_snapshot = _gateway(bid_source=malformed_source)
    malformed = _execute(
        malformed_gateway,
        call=_call(
            snapshot=malformed_snapshot,
            tool_name=BID_DOCUMENT_SEARCH,
            arguments={"query": "投标截止时间"},
            sequence=10,
        ),
        snapshot=malformed_snapshot,
    )
    assert malformed.result.error.code is ToolErrorCode.CONTRACT_VIOLATION


def test_gateway_rejects_expired_deadline_and_stale_visible_set() -> None:
    gateway, snapshot = _gateway()
    expired = _execute(
        gateway,
        call=_call(
            snapshot=snapshot,
            tool_name=BID_DOCUMENT_SEARCH,
            arguments={"query": "投标截止时间"},
            sequence=11,
        ),
        snapshot=snapshot,
        deadline=_deadline(seconds=-1),
    )
    assert expired.result.error.code is ToolErrorCode.DEADLINE_EXCEEDED
    assert expired.guard_decisions[-1].code == "DEADLINE_EXPIRED"

    stale_call = _call(
        snapshot=snapshot,
        tool_name=BID_DOCUMENT_SEARCH,
        arguments={"query": "投标截止时间"},
        sequence=12,
    ).model_copy(update={"visible_tools_hash": canonical_hash(["stale"] )})
    stale = _execute(gateway, call=stale_call, snapshot=snapshot)
    assert stale.result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert stale.guard_decisions[-1].code == "FROZEN_TOOL_SET_MISMATCH"


def test_provider_call_identity_reuse_is_not_executed_twice() -> None:
    gateway, snapshot = _gateway()
    first_call = _call(
        snapshot=snapshot,
        tool_name=BID_DOCUMENT_SEARCH,
        arguments={"query": "投标截止时间"},
        sequence=13,
        provider_id="provider-call:v602:shared",
    )
    first = _execute(gateway, call=first_call, snapshot=snapshot)
    assert first.result.ok

    reused_identity = _call(
        snapshot=snapshot,
        tool_name=ENTERPRISE_KNOWLEDGE_SEARCH,
        arguments={"query": "企业资质"},
        sequence=14,
        provider_id="provider-call:v602:shared",
    )
    rejected = _execute(gateway, call=reused_identity, snapshot=snapshot)
    assert not rejected.result.ok
    assert rejected.result.error.code is ToolErrorCode.UNAVAILABLE
    assert not rejected.accepted_for_context
