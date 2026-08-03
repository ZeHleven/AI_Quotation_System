from __future__ import annotations

from typing import Protocol, Sequence

from .contracts import (
    DocumentManifest,
    EvidenceBlock,
    EvidenceRefInput,
    EvidenceStructuralContext,
    VersionConflict,
)
from .retrieval_router import RetrievalMode


class TenderEvidenceRepository(Protocol):
    """Storage boundary for the MCP protocol/service layer."""

    def get_manifest(self, *, case_id: str) -> DocumentManifest: ...

    def search(
        self,
        *,
        case_id: str,
        query: str,
        top_k: int,
        search_mode: RetrievalMode = "hybrid",
    ) -> list[EvidenceBlock]: ...

    def get_context(
        self,
        *,
        case_id: str,
        evidence_id: str,
        before_blocks: int,
        after_blocks: int,
    ) -> list[EvidenceBlock]: ...

    def get_structural_context(
        self,
        *,
        case_id: str,
        evidence_ids: Sequence[str],
        max_heading_lookback: int = 12,
    ) -> dict[str, list[EvidenceStructuralContext]]: ...

    def get_document_versions(
        self,
        *,
        case_id: str,
        document_key: str,
    ) -> tuple[list[dict[str, object]], list[VersionConflict]]: ...

    def validate_refs(
        self,
        *,
        case_id: str,
        refs: Sequence[EvidenceRefInput],
        manifest_version: int,
    ) -> list[dict[str, object]]: ...

    def record_context_read(
        self,
        *,
        case_id: str,
        assessment_id: str,
        agent_run_id: str,
        subject: str,
        evidence_id: str,
        trace_id: str,
    ) -> None: ...

    def get_context_read_ids(
        self,
        *,
        case_id: str,
        assessment_id: str,
        agent_run_id: str,
        evidence_ids: Sequence[str],
    ) -> set[str]: ...
