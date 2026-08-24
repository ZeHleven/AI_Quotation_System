"""Canonical Tool sources over one explicitly supplied offline RAG backend."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field, model_validator

from .common import Reference, StrictContract
from .rag_adapters import (
    BidDocumentSearchSource,
    DocumentsOutlineSource,
    EnterpriseKnowledgeSearchSource,
    EvidenceReadSource,
    RagSourceUnavailable,
)
from .tool_guards import EvidenceScopeAuthorization
from .tool_runtime import BindingExecutionResult, ToolProvenanceRecord, canonical_hash
from .tools import (
    DocumentsOutlineOutput,
    EvidenceAtom,
    EvidenceCandidate,
    EvidenceCandidatesOutput,
    EvidenceReadOutput,
    ToolExecutionContext,
)


class OfflineEvidenceRecord(StrictContract):
    evidence_ref: Reference
    source_domain: str = Field(pattern=r"^(bid_document|enterprise_knowledge)$")
    source_scope_ref: Reference
    source_version_ref: Reference
    text: str = Field(min_length=1, max_length=20_000)
    locator: str = Field(min_length=1, max_length=1_000)


class OfflineSearchResult(StrictContract):
    records: tuple[OfflineEvidenceRecord, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_refs(self) -> "OfflineSearchResult":
        refs = tuple(item.evidence_ref for item in self.records)
        if len(refs) != len(set(refs)):
            raise ValueError("offline search evidence refs must be unique")
        return self


class OfflineRagBackend(Protocol):
    async def document_outline(self, document_ref: str) -> DocumentsOutlineOutput: ...

    async def search(
        self,
        *,
        source_domain: str,
        query: str,
        top_k: int,
    ) -> OfflineSearchResult: ...

    async def resolve(
        self,
        evidence_refs: tuple[str, ...],
    ) -> tuple[OfflineEvidenceRecord, ...]: ...


class CanonicalOfflineRagSources(
    DocumentsOutlineSource,
    BidDocumentSearchSource,
    EnterpriseKnowledgeSearchSource,
    EvidenceReadSource,
    EvidenceScopeAuthorization,
):
    """Turn one hybrid/vector backend into the four canonical read-only Tools."""

    def __init__(self, backend: OfflineRagBackend, *, search_top_k: int = 8) -> None:
        if not 1 <= int(search_top_k) <= 32:
            raise ValueError("offline RAG search_top_k must be between 1 and 32")
        self._backend = backend
        self._search_top_k = int(search_top_k)

    async def read_outline(
        self,
        *,
        document_ref: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        if document_ref not in context.authorized_document_refs:
            raise RagSourceUnavailable("document outline scope is unavailable")
        output = await self._backend.document_outline(document_ref)
        return BindingExecutionResult(
            structured_content=output,
            provenance=(
                ToolProvenanceRecord(
                    output_ref=document_ref,
                    source_domain="bid_document",
                    source_scope_ref=document_ref,
                    source_version_ref=document_ref,
                    content_hash=canonical_hash(output.model_dump(mode="json")),
                    locator="document:outline",
                    citable=False,
                ),
            ),
        )

    async def search_bid_documents(
        self,
        *,
        query: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        result = await self._backend.search(
            source_domain="bid_document",
            query=query,
            top_k=self._search_top_k,
        )
        return self._search_binding(result, context=context)

    async def search_enterprise_knowledge(
        self,
        *,
        query: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        result = await self._backend.search(
            source_domain="enterprise_knowledge",
            query=query,
            top_k=self._search_top_k,
        )
        return self._search_binding(result, context=context)

    async def read_evidence(
        self,
        *,
        evidence_refs: tuple[str, ...],
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        records = await self._backend.resolve(evidence_refs)
        if (
            tuple(item.evidence_ref for item in records) != evidence_refs
            or any(not self._scope_allowed(item, context) for item in records)
        ):
            raise RagSourceUnavailable("offline evidence scope is unavailable")
        output = EvidenceReadOutput(
            evidence=tuple(
                EvidenceAtom(
                    evidence_ref=item.evidence_ref,
                    text=item.text,
                    locator=item.locator,
                    citable=True,
                )
                for item in records
            )
        )
        return BindingExecutionResult(
            structured_content=output,
            provenance=tuple(
                ToolProvenanceRecord(
                    output_ref=item.evidence_ref,
                    source_domain=item.source_domain,
                    source_scope_ref=item.source_scope_ref,
                    source_version_ref=item.source_version_ref,
                    content_hash=canonical_hash(item.text),
                    locator=item.locator,
                    citable=True,
                )
                for item in records
            ),
        )

    async def allowed(
        self,
        *,
        evidence_ref: str,
        context: ToolExecutionContext,
    ) -> bool:
        try:
            records = await self._backend.resolve((evidence_ref,))
        except Exception:
            return False
        return (
            len(records) == 1
            and records[0].evidence_ref == evidence_ref
            and self._scope_allowed(records[0], context)
        )

    @staticmethod
    def _search_binding(
        result: OfflineSearchResult,
        *,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        if any(
            not CanonicalOfflineRagSources._scope_allowed(item, context)
            for item in result.records
        ):
            raise RagSourceUnavailable("offline search crossed an authorized scope")
        candidates = tuple(
            EvidenceCandidate(
                evidence_ref=item.evidence_ref,
                excerpt=item.text[:4_000],
                locator=item.locator,
                citable=False,
            )
            for item in result.records
        )
        output = EvidenceCandidatesOutput(candidates=candidates)
        return BindingExecutionResult(
            structured_content=output,
            provenance=tuple(
                ToolProvenanceRecord(
                    output_ref=item.evidence_ref,
                    source_domain=item.source_domain,
                    source_scope_ref=item.source_scope_ref,
                    source_version_ref=item.source_version_ref,
                    content_hash=canonical_hash(candidate.excerpt),
                    locator=item.locator,
                    citable=False,
                )
                for item, candidate in zip(result.records, candidates, strict=True)
            ),
        )

    @staticmethod
    def _scope_allowed(
        record: OfflineEvidenceRecord,
        context: ToolExecutionContext,
    ) -> bool:
        if record.source_domain == "bid_document":
            return record.source_scope_ref in context.authorized_document_refs
        return (
            context.enterprise_scope_ref is not None
            and record.source_scope_ref == context.enterprise_scope_ref
        )
