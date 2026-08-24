"""Visibility, execution, and provenance guards for the canonical Tool Gateway."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from pydantic import BaseModel

from .registry import (
    BID_DOCUMENT_SEARCH,
    DOCUMENTS_OUTLINE,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    EVIDENCE_READ,
)
from .runtime import ToolCallRequest
from .tool_runtime import (
    GuardDecision,
    RegistrySnapshot,
    ToolGuardPolicy,
    ToolProvenanceRecord,
    canonical_hash,
)
from .tools import (
    BidDocumentSearchInput,
    CanonicalToolDefinition,
    DocumentsOutlineInput,
    DocumentsOutlineOutput,
    EnterpriseKnowledgeSearchInput,
    EvidenceCandidatesOutput,
    EvidenceReadInput,
    EvidenceReadOutput,
    ToolExecutionContext,
)


class EvidenceScopeAuthorization(Protocol):
    async def allowed(
        self,
        *,
        evidence_ref: str,
        context: ToolExecutionContext,
    ) -> bool: ...


class DenyAllEvidenceScopeAuthorization:
    async def allowed(
        self,
        *,
        evidence_ref: str,
        context: ToolExecutionContext,
    ) -> bool:
        return False


class StaticEvidenceScopeAuthorization:
    """Explicit fake authorization map for later tests; it parses no opaque ref."""

    def __init__(self, evidence_scope_refs: Mapping[str, str]) -> None:
        self._scope_refs = MappingProxyType(dict(evidence_scope_refs))

    async def allowed(
        self,
        *,
        evidence_ref: str,
        context: ToolExecutionContext,
    ) -> bool:
        scope_ref = self._scope_refs.get(evidence_ref)
        if scope_ref is None:
            return False
        return scope_ref in set(context.authorized_document_refs) or (
            context.enterprise_scope_ref is not None
            and scope_ref == context.enterprise_scope_ref
        )


def _allow(code: str = "TOOL_ALLOWED", message: str = "tool is allowed") -> GuardDecision:
    return GuardDecision(allowed=True, code=code, message=message)


def _deny(code: str, message: str) -> GuardDecision:
    return GuardDecision(allowed=False, code=code, message=message)


class DefaultVisibilityGuard:
    """Project a permission-safe visible set after relevance routing."""

    def evaluate(
        self,
        *,
        definition: CanonicalToolDefinition,
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
    ) -> GuardDecision:
        identity = self._identity_decision(context=context, policy=policy)
        if not identity.allowed:
            return identity
        if not policy.runtime_enabled:
            return _deny("RUNTIME_DISABLED", "tool runtime is disabled")
        if definition.name not in policy.allowed_tool_names:
            return _deny("TOOL_NOT_ALLOWED", "tool is outside the policy allowlist")
        if definition.execution.kind == "disabled":
            return _deny("BINDING_DISABLED", "tool binding is unavailable")
        binding = self._binding_decision(definition=definition, policy=policy)
        if not binding.allowed:
            return binding
        safety = self._safety_decision(definition=definition, policy=policy)
        if not safety.allowed:
            return safety
        if definition.name in {DOCUMENTS_OUTLINE, BID_DOCUMENT_SEARCH} and not (
            context.authorized_document_refs
        ):
            return _deny("DOCUMENT_SCOPE_EMPTY", "no authorized bid document is bound")
        if (
            definition.name == ENTERPRISE_KNOWLEDGE_SEARCH
            and context.enterprise_scope_ref is None
        ):
            return _deny("ENTERPRISE_SCOPE_EMPTY", "no enterprise scope is authorized")
        if definition.name == EVIDENCE_READ and not (
            context.authorized_document_refs or context.enterprise_scope_ref
        ):
            return _deny("EVIDENCE_SCOPE_EMPTY", "no evidence source scope is authorized")
        return _allow("TOOL_VISIBLE", "tool may be projected to the model")

    @staticmethod
    def _identity_decision(
        *,
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
    ) -> GuardDecision:
        if (
            context.authorization_snapshot_ref != policy.authorization_snapshot_ref
            or context.user_ref != policy.user_ref
            or context.tenant_ref != policy.tenant_ref
            or context.task_ref != policy.task_ref
        ):
            return _deny("AUTHORITY_MISMATCH", "runtime authority context does not match")
        return _allow()

    @staticmethod
    def _binding_decision(
        *,
        definition: CanonicalToolDefinition,
        policy: ToolGuardPolicy,
    ) -> GuardDecision:
        if definition.execution.kind == "local" and not policy.allow_local:
            return _deny("LOCAL_BINDING_DENIED", "local tool bindings are disabled")
        if definition.execution.kind == "mcp" and not policy.allow_mcp:
            return _deny("MCP_BINDING_DENIED", "MCP tool bindings are disabled")
        return _allow()

    @staticmethod
    def _safety_decision(
        *,
        definition: CanonicalToolDefinition,
        policy: ToolGuardPolicy,
    ) -> GuardDecision:
        if definition.safety.effect != "read_only":
            return _deny("MUTATING_TOOL_DENIED", "B03 only permits read-only tools")
        if definition.safety.external_egress and not policy.allow_external_egress:
            return _deny("EXTERNAL_EGRESS_DENIED", "external data egress is not allowed")
        if (
            definition.safety.requires_approval
            and definition.name not in policy.approved_tool_names
        ):
            return _deny("APPROVAL_REQUIRED", "tool approval is required")
        return _allow()


class DefaultExecutionGuard:
    def __init__(
        self,
        *,
        evidence_authorization: EvidenceScopeAuthorization | None = None,
    ) -> None:
        self._evidence_authorization = (
            evidence_authorization or DenyAllEvidenceScopeAuthorization()
        )
        self._visibility = DefaultVisibilityGuard()

    async def evaluate(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        snapshot: RegistrySnapshot,
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
    ) -> GuardDecision:
        visible = self._visibility.evaluate(
            definition=definition,
            context=context,
            policy=policy,
        )
        if not visible.allowed:
            return visible
        if (
            call.task_ref != context.task_ref
            or call.authorization_snapshot_ref != context.authorization_snapshot_ref
        ):
            return _deny("CALL_SCOPE_MISMATCH", "tool call is outside the runtime scope")
        if call.registry_snapshot_ref != snapshot.snapshot_ref:
            return _deny("REGISTRY_SNAPSHOT_MISMATCH", "tool call uses a stale registry")
        if definition.name not in snapshot.visible_tool_names:
            return _deny("TOOL_NOT_VISIBLE", "tool was not visible for this model turn")
        if definition.name == DOCUMENTS_OUTLINE:
            if not isinstance(arguments, DocumentsOutlineInput):
                return _deny("ARGUMENT_CONTRACT_MISMATCH", "tool arguments are invalid")
            if arguments.document_ref not in context.authorized_document_refs:
                return _deny("DOCUMENT_ACCESS_DENIED", "document is outside the allowed scope")
        elif definition.name == BID_DOCUMENT_SEARCH:
            if not isinstance(arguments, BidDocumentSearchInput):
                return _deny("ARGUMENT_CONTRACT_MISMATCH", "tool arguments are invalid")
            if not context.authorized_document_refs:
                return _deny("DOCUMENT_SCOPE_EMPTY", "no authorized bid document is bound")
        elif definition.name == ENTERPRISE_KNOWLEDGE_SEARCH:
            if not isinstance(arguments, EnterpriseKnowledgeSearchInput):
                return _deny("ARGUMENT_CONTRACT_MISMATCH", "tool arguments are invalid")
            if context.enterprise_scope_ref is None:
                return _deny("ENTERPRISE_SCOPE_EMPTY", "no enterprise scope is authorized")
        elif definition.name == EVIDENCE_READ:
            if not isinstance(arguments, EvidenceReadInput):
                return _deny("ARGUMENT_CONTRACT_MISMATCH", "tool arguments are invalid")
            for evidence_ref in arguments.evidence_refs:
                if not await self._evidence_authorization.allowed(
                    evidence_ref=evidence_ref,
                    context=context,
                ):
                    return _deny(
                        "EVIDENCE_ACCESS_DENIED",
                        "one or more evidence references are outside the allowed scope",
                    )
        else:
            return _deny("UNKNOWN_CANONICAL_TOOL", "tool has no B03 execution policy")
        return _allow("TOOL_EXECUTION_ALLOWED", "tool execution is authorized")


class DefaultProvenanceGuard:
    """Validate bounded output lineage without trusting a Local/MCP adapter."""

    def validate(
        self,
        *,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        output: BaseModel,
        provenance: tuple[ToolProvenanceRecord, ...],
        context: ToolExecutionContext,
    ) -> GuardDecision:
        if definition.name == DOCUMENTS_OUTLINE:
            return self._outline(
                arguments=arguments,
                output=output,
                provenance=provenance,
                context=context,
            )
        if definition.name in {
            BID_DOCUMENT_SEARCH,
            ENTERPRISE_KNOWLEDGE_SEARCH,
        }:
            return self._search(
                tool_name=definition.name,
                output=output,
                provenance=provenance,
                context=context,
            )
        if definition.name == EVIDENCE_READ:
            return self._read(
                arguments=arguments,
                output=output,
                provenance=provenance,
                context=context,
            )
        return _deny("PROVENANCE_POLICY_MISSING", "tool provenance policy is missing")

    @staticmethod
    def _scope_allowed(
        record: ToolProvenanceRecord,
        context: ToolExecutionContext,
    ) -> bool:
        if record.source_domain == "bid_document":
            return record.source_scope_ref in context.authorized_document_refs
        return (
            context.enterprise_scope_ref is not None
            and record.source_scope_ref == context.enterprise_scope_ref
        )

    def _outline(
        self,
        *,
        arguments: BaseModel,
        output: BaseModel,
        provenance: tuple[ToolProvenanceRecord, ...],
        context: ToolExecutionContext,
    ) -> GuardDecision:
        if not isinstance(arguments, DocumentsOutlineInput) or not isinstance(
            output, DocumentsOutlineOutput
        ):
            return _deny("PROVENANCE_CONTRACT_MISMATCH", "outline contract mismatch")
        if len(provenance) != 1:
            return _deny("PROVENANCE_INCOMPLETE", "outline requires one source record")
        record = provenance[0]
        if (
            record.output_ref != arguments.document_ref
            or record.source_domain != "bid_document"
            or record.source_scope_ref != arguments.document_ref
            or record.citable
            or record.content_hash != canonical_hash(output.model_dump(mode="json"))
            or not self._scope_allowed(record, context)
        ):
            return _deny("PROVENANCE_INVALID", "outline source record is invalid")
        return _allow("PROVENANCE_ACCEPTED", "outline provenance is valid")

    def _search(
        self,
        *,
        tool_name: str,
        output: BaseModel,
        provenance: tuple[ToolProvenanceRecord, ...],
        context: ToolExecutionContext,
    ) -> GuardDecision:
        if not isinstance(output, EvidenceCandidatesOutput):
            return _deny("PROVENANCE_CONTRACT_MISMATCH", "search contract mismatch")
        records = {record.output_ref: record for record in provenance}
        if len(records) != len(provenance):
            return _deny("PROVENANCE_DUPLICATE", "search provenance contains duplicates")
        if set(records) != {candidate.evidence_ref for candidate in output.candidates}:
            return _deny("PROVENANCE_INCOMPLETE", "search candidate provenance is incomplete")
        expected_domain = (
            "bid_document" if tool_name == BID_DOCUMENT_SEARCH else "enterprise_knowledge"
        )
        for candidate in output.candidates:
            record = records[candidate.evidence_ref]
            if (
                record.source_domain != expected_domain
                or record.citable
                or record.locator != candidate.locator
                or record.content_hash != canonical_hash(candidate.excerpt)
                or not self._scope_allowed(record, context)
            ):
                return _deny("PROVENANCE_INVALID", "search candidate provenance is invalid")
        return _allow("PROVENANCE_ACCEPTED", "search provenance is valid")

    def _read(
        self,
        *,
        arguments: BaseModel,
        output: BaseModel,
        provenance: tuple[ToolProvenanceRecord, ...],
        context: ToolExecutionContext,
    ) -> GuardDecision:
        if not isinstance(arguments, EvidenceReadInput) or not isinstance(
            output, EvidenceReadOutput
        ):
            return _deny("PROVENANCE_CONTRACT_MISMATCH", "evidence read contract mismatch")
        records = {record.output_ref: record for record in provenance}
        atoms = {atom.evidence_ref: atom for atom in output.evidence}
        if len(records) != len(provenance) or len(atoms) != len(output.evidence):
            return _deny("PROVENANCE_DUPLICATE", "evidence output contains duplicates")
        if set(atoms) != set(arguments.evidence_refs) or set(records) != set(atoms):
            return _deny("PROVENANCE_INCOMPLETE", "evidence read must resolve every requested ref")
        for evidence_ref, atom in atoms.items():
            record = records[evidence_ref]
            if (
                not record.citable
                or record.locator != atom.locator
                or record.content_hash != canonical_hash(atom.text)
                or not self._scope_allowed(record, context)
            ):
                return _deny("PROVENANCE_INVALID", "evidence atom provenance is invalid")
        return _allow("PROVENANCE_ACCEPTED", "evidence provenance is valid")

