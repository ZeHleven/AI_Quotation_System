"""Canonical B03 adapters over disabled or explicit static RAG source ports.

No adapter reads a file, database, vector index, network service, or MCP server
on import. The static sources exist only as injectable fixtures for later tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Protocol

from pydantic import BaseModel

from .registry import (
    BID_DOCUMENT_SEARCH,
    DOCUMENTS_OUTLINE,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    EVIDENCE_READ,
    CanonicalToolRegistry,
    build_initial_registry,
)
from .tool_executor import LocalHandlerRegistry
from .tool_runtime import BindingExecutionResult, ExecutionDeadline
from .tools import (
    BidDocumentSearchInput,
    CanonicalToolDefinition,
    DocumentsOutlineInput,
    EnterpriseKnowledgeSearchInput,
    EvidenceReadInput,
    LocalExecution,
    ToolExecutionContext,
)


DOCUMENTS_OUTLINE_HANDLER_ID = "fake.documents_outline"
BID_DOCUMENT_SEARCH_HANDLER_ID = "fake.bid_document_search"
ENTERPRISE_KNOWLEDGE_SEARCH_HANDLER_ID = "fake.enterprise_knowledge_search"
EVIDENCE_READ_HANDLER_ID = "fake.evidence_read"

LOCAL_DOCUMENTS_OUTLINE_HANDLER_ID = "local.rag.documents_outline"
LOCAL_BID_DOCUMENT_SEARCH_HANDLER_ID = "local.rag.bid_document_search"
LOCAL_ENTERPRISE_KNOWLEDGE_SEARCH_HANDLER_ID = (
    "local.rag.enterprise_knowledge_search"
)
LOCAL_EVIDENCE_READ_HANDLER_ID = "local.rag.evidence_read"


class RagSourceUnavailable(RuntimeError):
    pass


class DocumentsOutlineSource(Protocol):
    async def read_outline(
        self,
        *,
        document_ref: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult: ...


class BidDocumentSearchSource(Protocol):
    async def search_bid_documents(
        self,
        *,
        query: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult: ...


class EnterpriseKnowledgeSearchSource(Protocol):
    async def search_enterprise_knowledge(
        self,
        *,
        query: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult: ...


class EvidenceReadSource(Protocol):
    async def read_evidence(
        self,
        *,
        evidence_refs: tuple[str, ...],
        context: ToolExecutionContext,
    ) -> BindingExecutionResult: ...


class DisabledRagSource:
    """Fail-closed source used when no local development fixture is configured."""

    async def read_outline(self, **_: object) -> BindingExecutionResult:
        raise RagSourceUnavailable("document outline source is not configured")

    async def search_bid_documents(self, **_: object) -> BindingExecutionResult:
        raise RagSourceUnavailable("bid document search source is not configured")

    async def search_enterprise_knowledge(self, **_: object) -> BindingExecutionResult:
        raise RagSourceUnavailable("enterprise knowledge source is not configured")

    async def read_evidence(self, **_: object) -> BindingExecutionResult:
        raise RagSourceUnavailable("evidence read source is not configured")


class StaticDocumentsOutlineSource:
    def __init__(self, results: Mapping[str, BindingExecutionResult]) -> None:
        self._results = MappingProxyType(dict(results))

    async def read_outline(
        self,
        *,
        document_ref: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        try:
            return self._results[document_ref]
        except KeyError as exc:
            raise RagSourceUnavailable("document outline fixture was not found") from exc


class StaticBidDocumentSearchSource:
    def __init__(self, results: Mapping[str, BindingExecutionResult]) -> None:
        self._results = MappingProxyType(dict(results))

    async def search_bid_documents(
        self,
        *,
        query: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        try:
            return self._results[query]
        except KeyError as exc:
            raise RagSourceUnavailable("bid document search fixture was not found") from exc


class StaticEnterpriseKnowledgeSearchSource:
    def __init__(self, results: Mapping[str, BindingExecutionResult]) -> None:
        self._results = MappingProxyType(dict(results))

    async def search_enterprise_knowledge(
        self,
        *,
        query: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        try:
            return self._results[query]
        except KeyError as exc:
            raise RagSourceUnavailable("enterprise knowledge fixture was not found") from exc


class StaticEvidenceReadSource:
    def __init__(
        self,
        results: Mapping[tuple[str, ...], BindingExecutionResult],
    ) -> None:
        self._results = MappingProxyType(dict(results))

    async def read_evidence(
        self,
        *,
        evidence_refs: tuple[str, ...],
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        try:
            return self._results[evidence_refs]
        except KeyError as exc:
            raise RagSourceUnavailable("evidence read fixture was not found") from exc


class DocumentsOutlineAdapter:
    def __init__(self, source: DocumentsOutlineSource) -> None:
        self._source = source

    async def execute(
        self,
        *,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        context: ToolExecutionContext,
        deadline: ExecutionDeadline,
    ) -> BindingExecutionResult:
        if not isinstance(arguments, DocumentsOutlineInput):
            raise TypeError("documents_outline requires DocumentsOutlineInput")
        return await self._source.read_outline(
            document_ref=arguments.document_ref,
            context=context,
        )


class BidDocumentSearchAdapter:
    def __init__(self, source: BidDocumentSearchSource) -> None:
        self._source = source

    async def execute(
        self,
        *,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        context: ToolExecutionContext,
        deadline: ExecutionDeadline,
    ) -> BindingExecutionResult:
        if not isinstance(arguments, BidDocumentSearchInput):
            raise TypeError("bid_document_search requires BidDocumentSearchInput")
        return await self._source.search_bid_documents(
            query=arguments.query,
            context=context,
        )


class EnterpriseKnowledgeSearchAdapter:
    def __init__(self, source: EnterpriseKnowledgeSearchSource) -> None:
        self._source = source

    async def execute(
        self,
        *,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        context: ToolExecutionContext,
        deadline: ExecutionDeadline,
    ) -> BindingExecutionResult:
        if not isinstance(arguments, EnterpriseKnowledgeSearchInput):
            raise TypeError(
                "enterprise_knowledge_search requires EnterpriseKnowledgeSearchInput"
            )
        return await self._source.search_enterprise_knowledge(
            query=arguments.query,
            context=context,
        )


class EvidenceReadAdapter:
    def __init__(self, source: EvidenceReadSource) -> None:
        self._source = source

    async def execute(
        self,
        *,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        context: ToolExecutionContext,
        deadline: ExecutionDeadline,
    ) -> BindingExecutionResult:
        if not isinstance(arguments, EvidenceReadInput):
            raise TypeError("evidence_read requires EvidenceReadInput")
        return await self._source.read_evidence(
            evidence_refs=arguments.evidence_refs,
            context=context,
        )


def build_fake_registry() -> CanonicalToolRegistry:
    """Opt-in Local bindings for explicit fixtures; never used by default config."""

    initial = build_initial_registry()
    handler_ids = {
        DOCUMENTS_OUTLINE: DOCUMENTS_OUTLINE_HANDLER_ID,
        BID_DOCUMENT_SEARCH: BID_DOCUMENT_SEARCH_HANDLER_ID,
        ENTERPRISE_KNOWLEDGE_SEARCH: ENTERPRISE_KNOWLEDGE_SEARCH_HANDLER_ID,
        EVIDENCE_READ: EVIDENCE_READ_HANDLER_ID,
    }
    return CanonicalToolRegistry(
        replace(
            initial.get(name),
            execution=LocalExecution(handler_id=handler_ids[name]),
        )
        for name in initial.names
    )


def build_fake_handler_registry(
    *,
    outline_source: DocumentsOutlineSource,
    bid_search_source: BidDocumentSearchSource,
    enterprise_search_source: EnterpriseKnowledgeSearchSource,
    evidence_read_source: EvidenceReadSource,
) -> LocalHandlerRegistry:
    return LocalHandlerRegistry(
        (
            (DOCUMENTS_OUTLINE_HANDLER_ID, DocumentsOutlineAdapter(outline_source)),
            (BID_DOCUMENT_SEARCH_HANDLER_ID, BidDocumentSearchAdapter(bid_search_source)),
            (
                ENTERPRISE_KNOWLEDGE_SEARCH_HANDLER_ID,
                EnterpriseKnowledgeSearchAdapter(enterprise_search_source),
            ),
            (EVIDENCE_READ_HANDLER_ID, EvidenceReadAdapter(evidence_read_source)),
        )
    )


def build_local_rag_registry() -> CanonicalToolRegistry:
    """Bind the four declarations to explicit local read-only handlers."""

    initial = build_initial_registry()
    handler_ids = {
        DOCUMENTS_OUTLINE: LOCAL_DOCUMENTS_OUTLINE_HANDLER_ID,
        BID_DOCUMENT_SEARCH: LOCAL_BID_DOCUMENT_SEARCH_HANDLER_ID,
        ENTERPRISE_KNOWLEDGE_SEARCH: LOCAL_ENTERPRISE_KNOWLEDGE_SEARCH_HANDLER_ID,
        EVIDENCE_READ: LOCAL_EVIDENCE_READ_HANDLER_ID,
    }
    return CanonicalToolRegistry(
        replace(
            initial.get(name),
            execution=LocalExecution(handler_id=handler_ids[name]),
        )
        for name in initial.names
    )


def build_local_rag_handler_registry(
    *,
    outline_source: DocumentsOutlineSource,
    bid_search_source: BidDocumentSearchSource,
    enterprise_search_source: EnterpriseKnowledgeSearchSource,
    evidence_read_source: EvidenceReadSource,
) -> LocalHandlerRegistry:
    """Bind supplied offline sources without reading or starting them."""

    return LocalHandlerRegistry(
        (
            (
                LOCAL_DOCUMENTS_OUTLINE_HANDLER_ID,
                DocumentsOutlineAdapter(outline_source),
            ),
            (
                LOCAL_BID_DOCUMENT_SEARCH_HANDLER_ID,
                BidDocumentSearchAdapter(bid_search_source),
            ),
            (
                LOCAL_ENTERPRISE_KNOWLEDGE_SEARCH_HANDLER_ID,
                EnterpriseKnowledgeSearchAdapter(enterprise_search_source),
            ),
            (
                LOCAL_EVIDENCE_READ_HANDLER_ID,
                EvidenceReadAdapter(evidence_read_source),
            ),
        )
    )
