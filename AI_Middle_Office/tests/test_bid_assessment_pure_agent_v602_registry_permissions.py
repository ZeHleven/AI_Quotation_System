from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.bid_assessment_pure.planning import (
    ExecutionMode,
    InformationSourceHint,
    IntentUnderstanding,
)
from app.agents.bid_assessment_pure.rag_adapters import build_fake_registry
from app.agents.bid_assessment_pure.registry import (
    BID_DOCUMENT_SEARCH,
    DOCUMENTS_OUTLINE,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    EVIDENCE_READ,
    INITIAL_TOOL_NAMES,
    CanonicalToolRegistry,
    build_initial_registry,
)
from app.agents.bid_assessment_pure.runtime import ToolCallRequest
from app.agents.bid_assessment_pure.slots import PendingContext, PendingPhase
from app.agents.bid_assessment_pure.state import AgentTaskState, AgentTaskStatus
from app.agents.bid_assessment_pure.state_machine import create_running_task
from app.agents.bid_assessment_pure.tool_executor import (
    CanonicalToolExecutor,
    LocalHandlerRegistry,
    McpClientRegistry,
    ToolBindingUnavailable,
    ToolDeadlineExceeded,
)
from app.agents.bid_assessment_pure.tool_gateway import ToolVisibilityProjector
from app.agents.bid_assessment_pure.tool_guards import (
    DefaultExecutionGuard,
    DefaultProvenanceGuard,
    DefaultVisibilityGuard,
    StaticEvidenceScopeAuthorization,
)
from app.agents.bid_assessment_pure.tool_router import RelevanceToolRouter
from app.agents.bid_assessment_pure.tool_runtime import (
    BindingExecutionResult,
    ExecutionDeadline,
    ToolGuardPolicy,
    ToolProvenanceRecord,
    canonical_hash,
    freeze_registry_snapshot,
)
from app.agents.bid_assessment_pure.tools import (
    BidDocumentSearchInput,
    DocumentsOutlineInput,
    EnterpriseKnowledgeSearchInput,
    EvidenceAtom,
    EvidenceCandidate,
    EvidenceCandidatesOutput,
    EvidenceReadInput,
    EvidenceReadOutput,
    LocalExecution,
    McpExecution,
    ToolSafety,
    ToolExecutionContext,
)


def _task(
    *,
    status: AgentTaskStatus = AgentTaskStatus.RUNNING,
    state_version: int = 3,
    in_flight_action_ref: str | None = "action:v602",
) -> AgentTaskState:
    values = create_running_task(
        task_id="task:v602",
        session_id="conversation:v602",
        goal_ref="goal:v602",
    ).model_dump(mode="python")
    values.update(
        status=status,
        state_version=state_version,
        in_flight_action_ref=in_flight_action_ref,
    )
    if status is AgentTaskStatus.PENDING:
        values.update(
            pending_context=PendingContext(
                slot_ref="slot:v602",
                checkpoint_ref="checkpoint:v602",
                phase=PendingPhase.WAITING_INPUT,
                validation_attempt_ref=None,
                last_error_ref=None,
            ),
            in_flight_action_ref=None,
        )
    return AgentTaskState.model_validate(values)


def _context(
    *,
    tenant_ref: str = "tenant:v602",
    documents: tuple[str, ...] = ("document:bid-1",),
    enterprise_scope_ref: str | None = "enterprise:scope-1",
) -> ToolExecutionContext:
    return ToolExecutionContext(
        user_ref="user:v602",
        tenant_ref=tenant_ref,
        conversation_ref="conversation:v602",
        task_ref="task:v602",
        state_version=3,
        context_snapshot_ref="context:v602",
        authorization_snapshot_ref="authorization:v602",
        authorized_document_refs=documents,
        enterprise_scope_ref=enterprise_scope_ref,
    )


def _policy(
    *,
    allowed_names: tuple[str, ...] = INITIAL_TOOL_NAMES,
    runtime_enabled: bool = True,
    allow_local: bool = True,
    allow_mcp: bool = False,
    tenant_ref: str = "tenant:v602",
) -> ToolGuardPolicy:
    return ToolGuardPolicy(
        authorization_snapshot_ref="authorization:v602",
        user_ref="user:v602",
        tenant_ref=tenant_ref,
        task_ref="task:v602",
        runtime_enabled=runtime_enabled,
        allowed_tool_names=allowed_names,
        allow_local=allow_local,
        allow_mcp=allow_mcp,
        allow_external_egress=False,
        approved_tool_names=(),
    )


def _understanding(
    *hints: InformationSourceHint,
    clarification_needed: bool = False,
) -> IntentUnderstanding:
    return IntentUnderstanding(
        goal_summary="判断招标要求与企业能力是否匹配",
        information_needs=("资格要求", "企业资质"),
        source_hints=hints,
        clarification_needed=clarification_needed,
        blocking_slot_name=("assessment.documents" if clarification_needed else None),
        execution_mode=ExecutionMode.DIRECT,
        rationale="根据当前问题动态选择信息来源",
    )


def _call(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    snapshot,
    sequence: int = 1,
) -> ToolCallRequest:
    return ToolCallRequest(
        call_ref=f"call:v602:{sequence}",
        provider_tool_call_id=f"provider-call:v602:{sequence}",
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


def _deadline(*, seconds: float = 5) -> ExecutionDeadline:
    return ExecutionDeadline(
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=seconds)
    )


def test_initial_registry_is_fail_closed_and_model_contract_is_minimal() -> None:
    registry = build_initial_registry()
    assert registry.names == INITIAL_TOOL_NAMES
    for name in registry.names:
        definition = registry.get(name)
        assert definition.execution.kind == "disabled"
        visible = definition.model_visible_contract().model_dump(mode="python")
        assert set(visible) == {"name", "description", "input_schema"}
        assert "output_schema" not in visible
        assert "execution" not in visible
        assert "safety" not in visible
        serialized = str(visible["input_schema"])
        assert "tenant_ref" not in serialized
        assert "authorization_snapshot_ref" not in serialized
        assert "context_snapshot_ref" not in serialized


def test_registry_snapshot_freezes_full_contract_and_dynamic_visible_set() -> None:
    registry = build_fake_registry()
    snapshot = freeze_registry_snapshot(
        registry,
        visible_names=(BID_DOCUMENT_SEARCH, EVIDENCE_READ),
    )
    repeated = freeze_registry_snapshot(
        registry,
        visible_names=(BID_DOCUMENT_SEARCH, EVIDENCE_READ),
    )
    reordered = freeze_registry_snapshot(
        registry,
        visible_names=(EVIDENCE_READ, BID_DOCUMENT_SEARCH),
    )
    assert snapshot == repeated
    assert snapshot.snapshot_hash == repeated.snapshot_hash
    assert snapshot.visible_tool_names == (BID_DOCUMENT_SEARCH, EVIDENCE_READ)
    assert snapshot.visible_tools_hash != reordered.visible_tools_hash
    assert len(snapshot.entries) == 4
    assert all(entry.execution_kind == "local" for entry in snapshot.entries)
    assert tuple(item.name for item in snapshot.model_visible_contracts()) == (
        BID_DOCUMENT_SEARCH,
        EVIDENCE_READ,
    )


def test_router_is_relevance_only_and_returns_no_tools_while_clarifying_or_pending() -> None:
    registry = build_fake_registry()
    router = RelevanceToolRouter()
    visible = router.visible_tool_names(
        task=_task(),
        understanding=_understanding(
            InformationSourceHint.BID_DOCUMENTS,
            InformationSourceHint.ENTERPRISE_KNOWLEDGE,
            InformationSourceHint.EXISTING_EVIDENCE,
        ),
        registry=registry,
    )
    assert visible == INITIAL_TOOL_NAMES
    assert router.visible_tool_names(
        task=_task(),
        understanding=_understanding(
            InformationSourceHint.BID_DOCUMENTS,
            clarification_needed=True,
        ),
        registry=registry,
    ) == ()
    assert router.visible_tool_names(
        task=_task(status=AgentTaskStatus.PENDING),
        understanding=_understanding(InformationSourceHint.BID_DOCUMENTS),
        registry=registry,
    ) == ()


def test_visibility_projection_intersects_relevance_policy_binding_and_scope() -> None:
    registry = build_fake_registry()
    projection = ToolVisibilityProjector().project(
        registry=registry,
        relevant_names=INITIAL_TOOL_NAMES,
        context=_context(),
        policy=_policy(
            allowed_names=(BID_DOCUMENT_SEARCH, ENTERPRISE_KNOWLEDGE_SEARCH),
        ),
    )
    assert projection.snapshot.visible_tool_names == (
        BID_DOCUMENT_SEARCH,
        ENTERPRISE_KNOWLEDGE_SEARCH,
    )
    decisions = {name: decision for name, decision in projection.decisions}
    assert decisions[BID_DOCUMENT_SEARCH].code == "TOOL_VISIBLE"
    assert decisions[DOCUMENTS_OUTLINE].code == "TOOL_NOT_ALLOWED"

    no_enterprise = ToolVisibilityProjector().project(
        registry=registry,
        relevant_names=(ENTERPRISE_KNOWLEDGE_SEARCH,),
        context=_context(enterprise_scope_ref=None),
        policy=_policy(allowed_names=(ENTERPRISE_KNOWLEDGE_SEARCH,)),
    )
    assert no_enterprise.snapshot.visible_tool_names == ()
    assert no_enterprise.decisions[0][1].code == "ENTERPRISE_SCOPE_EMPTY"


@pytest.mark.parametrize(
    ("context", "policy", "expected_code"),
    (
        (_context(tenant_ref="tenant:other"), _policy(), "AUTHORITY_MISMATCH"),
        (_context(), _policy(runtime_enabled=False), "RUNTIME_DISABLED"),
        (_context(), _policy(allow_local=False), "LOCAL_BINDING_DENIED"),
        (_context(documents=()), _policy(), "DOCUMENT_SCOPE_EMPTY"),
    ),
)
def test_visibility_guard_fails_closed(
    context: ToolExecutionContext,
    policy: ToolGuardPolicy,
    expected_code: str,
) -> None:
    definition = build_fake_registry().get(BID_DOCUMENT_SEARCH)
    decision = DefaultVisibilityGuard().evaluate(
        definition=definition,
        context=context,
        policy=policy,
    )
    assert not decision.allowed
    assert decision.code == expected_code


def test_visibility_guard_enforces_binding_and_safety_allowlists() -> None:
    guard = DefaultVisibilityGuard()
    context = _context()
    base = build_fake_registry().get(BID_DOCUMENT_SEARCH)

    disabled = build_initial_registry().get(BID_DOCUMENT_SEARCH)
    assert guard.evaluate(
        definition=disabled,
        context=context,
        policy=_policy(),
    ).code == "BINDING_DISABLED"

    mcp = replace(
        base,
        execution=McpExecution(
            server_id="server:v602",
            remote_tool_name="remote_bid_search",
        ),
    )
    assert guard.evaluate(
        definition=mcp,
        context=context,
        policy=_policy(allow_local=False, allow_mcp=False),
    ).code == "MCP_BINDING_DENIED"
    assert guard.evaluate(
        definition=mcp,
        context=context,
        policy=_policy(allow_local=False, allow_mcp=True),
    ).allowed

    mutating = replace(
        base,
        safety=ToolSafety(
            effect="mutating",
            data_scope="context_bound",
            external_egress=False,
            requires_approval=False,
        ),
    )
    assert guard.evaluate(
        definition=mutating,
        context=context,
        policy=_policy(),
    ).code == "MUTATING_TOOL_DENIED"

    external = replace(
        base,
        safety=ToolSafety(
            effect="read_only",
            data_scope="context_bound",
            external_egress=True,
            requires_approval=False,
        ),
    )
    assert guard.evaluate(
        definition=external,
        context=context,
        policy=_policy(),
    ).code == "EXTERNAL_EGRESS_DENIED"

    approval = replace(
        base,
        safety=ToolSafety(
            effect="read_only",
            data_scope="context_bound",
            external_egress=False,
            requires_approval=True,
        ),
    )
    assert guard.evaluate(
        definition=approval,
        context=context,
        policy=_policy(),
    ).code == "APPROVAL_REQUIRED"


def test_execution_guard_enforces_document_evidence_and_frozen_snapshot_scope() -> None:
    registry = build_fake_registry()
    context = _context()
    policy = _policy()
    snapshot = freeze_registry_snapshot(
        registry,
        visible_names=(DOCUMENTS_OUTLINE, EVIDENCE_READ),
    )
    evidence_guard = DefaultExecutionGuard(
        evidence_authorization=StaticEvidenceScopeAuthorization(
            {
                "evidence:bid-1": "document:bid-1",
                "evidence:enterprise-1": "enterprise:scope-1",
            }
        )
    )

    allowed_outline = asyncio.run(
        evidence_guard.evaluate(
            call=_call(
                tool_name=DOCUMENTS_OUTLINE,
                arguments={"document_ref": "document:bid-1"},
                snapshot=snapshot,
            ),
            definition=registry.get(DOCUMENTS_OUTLINE),
            arguments=DocumentsOutlineInput(document_ref="document:bid-1"),
            snapshot=snapshot,
            context=context,
            policy=policy,
        )
    )
    assert allowed_outline.allowed

    denied_outline = asyncio.run(
        evidence_guard.evaluate(
            call=_call(
                tool_name=DOCUMENTS_OUTLINE,
                arguments={"document_ref": "document:outside"},
                snapshot=snapshot,
                sequence=2,
            ),
            definition=registry.get(DOCUMENTS_OUTLINE),
            arguments=DocumentsOutlineInput(document_ref="document:outside"),
            snapshot=snapshot,
            context=context,
            policy=policy,
        )
    )
    assert denied_outline.code == "DOCUMENT_ACCESS_DENIED"

    allowed_evidence = asyncio.run(
        evidence_guard.evaluate(
            call=_call(
                tool_name=EVIDENCE_READ,
                arguments={"evidence_refs": ["evidence:bid-1", "evidence:enterprise-1"]},
                snapshot=snapshot,
                sequence=3,
            ),
            definition=registry.get(EVIDENCE_READ),
            arguments=EvidenceReadInput(
                evidence_refs=("evidence:bid-1", "evidence:enterprise-1")
            ),
            snapshot=snapshot,
            context=context,
            policy=policy,
        )
    )
    assert allowed_evidence.allowed

    denied_evidence = asyncio.run(
        evidence_guard.evaluate(
            call=_call(
                tool_name=EVIDENCE_READ,
                arguments={"evidence_refs": ["evidence:outside"]},
                snapshot=snapshot,
                sequence=4,
            ),
            definition=registry.get(EVIDENCE_READ),
            arguments=EvidenceReadInput(evidence_refs=("evidence:outside",)),
            snapshot=snapshot,
            context=context,
            policy=policy,
        )
    )
    assert denied_evidence.code == "EVIDENCE_ACCESS_DENIED"


def test_provenance_guard_rejects_cross_domain_cross_scope_and_non_citable_atoms() -> None:
    context = _context()
    registry = build_fake_registry()
    guard = DefaultProvenanceGuard()
    candidate = EvidenceCandidate(
        evidence_ref="evidence:bid-1",
        excerpt="投标截止时间为 2026 年 9 月 1 日。",
        locator="page:12",
        citable=False,
    )
    output = EvidenceCandidatesOutput(candidates=(candidate,))
    valid_record = ToolProvenanceRecord(
        output_ref=candidate.evidence_ref,
        source_domain="bid_document",
        source_scope_ref="document:bid-1",
        source_version_ref="document-version:1",
        content_hash=canonical_hash(candidate.excerpt),
        locator=candidate.locator,
        citable=False,
    )
    accepted = guard.validate(
        definition=registry.get(BID_DOCUMENT_SEARCH),
        arguments=BidDocumentSearchInput(query="投标截止时间"),
        output=output,
        provenance=(valid_record,),
        context=context,
    )
    assert accepted.allowed

    wrong_domain = valid_record.model_copy(
        update={
            "source_domain": "enterprise_knowledge",
            "source_scope_ref": "enterprise:scope-1",
        }
    )
    denied = guard.validate(
        definition=registry.get(BID_DOCUMENT_SEARCH),
        arguments=BidDocumentSearchInput(query="投标截止时间"),
        output=output,
        provenance=(wrong_domain,),
        context=context,
    )
    assert denied.code == "PROVENANCE_INVALID"

    atom = EvidenceAtom(
        evidence_ref="evidence:bid-1",
        text="投标截止时间为 2026 年 9 月 1 日。",
        locator="page:12",
        citable=True,
    )
    atom_output = EvidenceReadOutput(evidence=(atom,))
    atom_record = valid_record.model_copy(
        update={"content_hash": canonical_hash(atom.text), "citable": False}
    )
    denied_atom = guard.validate(
        definition=registry.get(EVIDENCE_READ),
        arguments=EvidenceReadInput(evidence_refs=(atom.evidence_ref,)),
        output=atom_output,
        provenance=(atom_record,),
        context=context,
    )
    assert denied_atom.code == "PROVENANCE_INVALID"


class RecordingLocalHandler:
    def __init__(self) -> None:
        self.arguments: Any = None
        self.context: ToolExecutionContext | None = None

    async def execute(self, *, definition, arguments, context, deadline):
        self.arguments = arguments
        self.context = context
        return BindingExecutionResult(structured_content={"ok": True}, provenance=())


class RecordingMcpClient:
    def __init__(self) -> None:
        self.remote_tool_name: str | None = None
        self.arguments: dict[str, Any] | None = None
        self.context: ToolExecutionContext | None = None

    async def execute_structured(
        self,
        *,
        remote_tool_name,
        arguments,
        context,
        deadline,
    ):
        self.remote_tool_name = remote_tool_name
        self.arguments = arguments
        self.context = context
        return BindingExecutionResult(structured_content={"ok": True}, provenance=())


def test_executor_dispatches_local_and_mcp_without_merging_runtime_context_into_arguments() -> None:
    base = build_initial_registry().get(BID_DOCUMENT_SEARCH)
    local_handler = RecordingLocalHandler()
    local_definition = replace(
        base,
        execution=LocalExecution(handler_id="handler:v602"),
    )
    local_executor = CanonicalToolExecutor(
        local_handlers=LocalHandlerRegistry((("handler:v602", local_handler),))
    )
    arguments = BidDocumentSearchInput(query="资格要求")
    context = _context()
    asyncio.run(
        local_executor.execute(
            definition=local_definition,
            arguments=arguments,
            context=context,
            deadline=_deadline(),
        )
    )
    assert local_handler.arguments == arguments
    assert local_handler.context == context

    mcp_client = RecordingMcpClient()
    mcp_definition = replace(
        base,
        execution=McpExecution(
            server_id="server:v602",
            remote_tool_name="remote_bid_search",
        ),
    )
    mcp_executor = CanonicalToolExecutor(
        mcp_clients=McpClientRegistry((("server:v602", mcp_client),))
    )
    asyncio.run(
        mcp_executor.execute(
            definition=mcp_definition,
            arguments=arguments,
            context=context,
            deadline=_deadline(),
        )
    )
    assert mcp_client.remote_tool_name == "remote_bid_search"
    assert mcp_client.arguments == {"query": "资格要求"}
    assert mcp_client.context == context
    assert "tenant_ref" not in mcp_client.arguments
    assert "authorization_snapshot_ref" not in mcp_client.arguments


def test_executor_fails_closed_for_disabled_missing_and_expired_bindings() -> None:
    disabled = build_initial_registry().get(BID_DOCUMENT_SEARCH)
    executor = CanonicalToolExecutor()
    with pytest.raises(ToolBindingUnavailable):
        asyncio.run(
            executor.execute(
                definition=disabled,
                arguments=BidDocumentSearchInput(query="资格要求"),
                context=_context(),
                deadline=_deadline(),
            )
        )

    missing = replace(
        disabled,
        execution=LocalExecution(handler_id="handler:missing"),
    )
    with pytest.raises(ToolBindingUnavailable):
        asyncio.run(
            executor.execute(
                definition=missing,
                arguments=BidDocumentSearchInput(query="资格要求"),
                context=_context(),
                deadline=_deadline(),
            )
        )

    with pytest.raises(ToolDeadlineExceeded):
        asyncio.run(
            executor.execute(
                definition=missing,
                arguments=BidDocumentSearchInput(query="资格要求"),
                context=_context(),
                deadline=_deadline(seconds=-1),
            )
        )


def test_tool_input_contract_rejects_authority_fields_from_model_arguments() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EnterpriseKnowledgeSearchInput.model_validate(
            {
                "query": "企业资质",
                "tenant_ref": "tenant:attacker",
                "enterprise_scope_ref": "enterprise:attacker",
            }
        )
