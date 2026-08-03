from __future__ import annotations

import json
import hashlib
import re
import threading
from pathlib import Path
from typing import Sequence

from .contracts import (
    DocumentManifest,
    EvidenceBlock,
    EvidenceRefInput,
    EvidenceStructuralContext,
    TenderCaseDataset,
    TenderEvidenceFile,
    VersionConflict,
)
from .retrieval_router import RetrievalMode
from .structure_context import build_structural_context_map


class TenderEvidenceRepositoryError(RuntimeError):
    """Base class for repository errors safe to translate at the service layer."""


class TenderCaseNotFoundError(TenderEvidenceRepositoryError):
    pass


class EvidenceNotFoundError(TenderEvidenceRepositoryError):
    pass


class LocalTenderEvidenceRepository:
    """Deterministic development repository backed by one validated JSON file.

    This adapter intentionally provides lexical search only. It proves the MCP
    contract and security boundary without claiming that MySQL, object storage,
    OCR, or vector retrieval are already connected.
    """

    def __init__(self, dataset_path: str | Path):
        self._dataset_path = Path(dataset_path).resolve()
        if not self._dataset_path.is_file():
            raise FileNotFoundError(
                f"tender evidence dataset does not exist: {self._dataset_path}"
            )
        payload = json.loads(self._dataset_path.read_text(encoding="utf-8"))
        dataset = TenderEvidenceFile.model_validate(payload)
        for case in dataset.cases:
            for block in case.blocks:
                calculated_hash = hashlib.sha256(
                    block.content.encode("utf-8")
                ).hexdigest()
                if calculated_hash != block.content_hash:
                    raise ValueError(
                        "evidence content_hash mismatch in local dataset: "
                        f"{block.evidence_id}"
                    )
        self._cases = {item.case_id: item for item in dataset.cases}
        self._read_trace_lock = threading.Lock()
        self._read_traces: set[tuple[str, str, str, str]] = set()

    @property
    def dataset_path(self) -> Path:
        return self._dataset_path

    def _get_case(self, case_id: str) -> TenderCaseDataset:
        case = self._cases.get(case_id)
        if case is None:
            raise TenderCaseNotFoundError("the scoped tender case does not exist")
        return case

    def get_manifest(self, *, case_id: str) -> DocumentManifest:
        return self._get_case(case_id).manifest.model_copy(deep=True)

    def search(
        self,
        *,
        case_id: str,
        query: str,
        top_k: int,
        search_mode: RetrievalMode = "hybrid",
    ) -> list[EvidenceBlock]:
        del search_mode
        case = self._get_case(case_id)
        terms = _query_terms(query)
        active_documents = {
            (item.document_id, item.document_version)
            for item in case.manifest.documents
            if item.active
        }
        ranked: list[tuple[int, int, EvidenceBlock]] = []
        for block in case.blocks:
            score = _score_block(block, query=query, terms=terms)
            if score <= 0:
                continue
            if (block.document_id, block.document_version) in active_documents:
                score += 3
            ranked.append((score, -block.block_order, block))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2].model_copy(deep=True) for item in ranked[:top_k]]

    def get_context(
        self,
        *,
        case_id: str,
        evidence_id: str,
        before_blocks: int,
        after_blocks: int,
    ) -> list[EvidenceBlock]:
        case = self._get_case(case_id)
        selected = next(
            (item for item in case.blocks if item.evidence_id == evidence_id),
            None,
        )
        if selected is None:
            raise EvidenceNotFoundError(
                "the evidence_id does not exist in the scoped tender case"
            )

        document_blocks = sorted(
            (
                item
                for item in case.blocks
                if item.document_id == selected.document_id
                and item.document_version == selected.document_version
            ),
            key=lambda item: item.block_order,
        )
        selected_index = next(
            index
            for index, item in enumerate(document_blocks)
            if item.evidence_id == evidence_id
        )
        start = max(0, selected_index - before_blocks)
        stop = min(len(document_blocks), selected_index + after_blocks + 1)
        return [item.model_copy(deep=True) for item in document_blocks[start:stop]]

    def get_structural_context(
        self,
        *,
        case_id: str,
        evidence_ids: Sequence[str],
        max_heading_lookback: int = 12,
    ) -> dict[str, list[EvidenceStructuralContext]]:
        case = self._get_case(case_id)
        requested = set(evidence_ids)
        if not requested:
            return {}
        selected_documents = {
            (item.document_id, item.document_version)
            for item in case.blocks
            if item.evidence_id in requested
        }
        document_blocks = [
            item.model_copy(deep=True)
            for item in case.blocks
            if (item.document_id, item.document_version)
            in selected_documents
        ]
        return build_structural_context_map(
            candidate_evidence_ids=evidence_ids,
            document_blocks=document_blocks,
            max_heading_lookback=max_heading_lookback,
        )

    def get_document_versions(
        self,
        *,
        case_id: str,
        document_key: str,
    ) -> tuple[list[dict[str, object]], list[VersionConflict]]:
        case = self._get_case(case_id)
        documents = [
            item
            for item in case.manifest.documents
            if item.document_key == document_key
        ]
        documents.sort(key=lambda item: item.document_version)
        versions = [
            {
                "document_id": item.document_id,
                "document_key": item.document_key,
                "file_name": item.file_name,
                "document_type": item.document_type,
                "document_version": item.document_version,
                "sha256": item.sha256,
                "parse_status": item.parse_status,
                "active": item.active,
            }
            for item in documents
        ]
        conflicts = [
            item.model_copy(deep=True)
            for item in case.version_conflicts.get(document_key, [])
        ]
        return versions, conflicts

    def validate_refs(
        self,
        *,
        case_id: str,
        refs: Sequence[EvidenceRefInput],
        manifest_version: int,
    ) -> list[dict[str, object]]:
        case = self._get_case(case_id)
        by_evidence_id = {item.evidence_id: item for item in case.blocks}
        active_documents = {
            (item.document_id, item.document_version)
            for item in case.manifest.documents
            if item.active and item.parse_status != "failed"
        }
        is_current_manifest = case.manifest.manifest_version == manifest_version
        validation: list[dict[str, object]] = []
        for ref in refs:
            stored = by_evidence_id.get(ref.evidence_id)
            reasons: list[str] = []
            if not is_current_manifest:
                reasons.append("manifest_version_mismatch")
            if stored is None:
                reasons.append("evidence_not_found")
            else:
                if stored.block_id != ref.block_id:
                    reasons.append("block_id_mismatch")
                if stored.document_id != ref.document_id:
                    reasons.append("document_id_mismatch")
                if stored.document_version != ref.document_version:
                    reasons.append("document_version_mismatch")
                if stored.content_hash != ref.content_hash:
                    reasons.append("content_hash_mismatch")
                if (
                    stored.document_id,
                    stored.document_version,
                ) not in active_documents:
                    reasons.append("document_version_not_active")
            validation.append(
                {
                    "evidence_id": ref.evidence_id,
                    "valid": not reasons,
                    "reasons": reasons,
                }
            )
        return validation

    def record_context_read(
        self,
        *,
        case_id: str,
        assessment_id: str,
        agent_run_id: str,
        subject: str,
        evidence_id: str,
        trace_id: str,
    ) -> None:
        del subject, trace_id
        case = self._get_case(case_id)
        if not any(item.evidence_id == evidence_id for item in case.blocks):
            raise EvidenceNotFoundError(
                "the evidence_id does not exist in the scoped tender case"
            )
        with self._read_trace_lock:
            self._read_traces.add(
                (case_id, assessment_id, agent_run_id, evidence_id)
            )

    def get_context_read_ids(
        self,
        *,
        case_id: str,
        assessment_id: str,
        agent_run_id: str,
        evidence_ids: Sequence[str],
    ) -> set[str]:
        self._get_case(case_id)
        requested = set(evidence_ids)
        with self._read_trace_lock:
            return {
                evidence_id
                for (
                    traced_case_id,
                    traced_assessment_id,
                    traced_run_id,
                    evidence_id,
                ) in self._read_traces
                if traced_case_id == case_id
                and traced_assessment_id == assessment_id
                and traced_run_id == agent_run_id
                and evidence_id in requested
            }


def _query_terms(query: str) -> list[str]:
    normalized = query.casefold().strip()
    if not normalized:
        return []
    raw_terms = re.findall(r"[a-z0-9_.-]+|[\u4e00-\u9fff]+", normalized)
    terms: set[str] = set()
    for raw in raw_terms:
        terms.add(raw)
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw) and len(raw) > 2:
            terms.update(raw[index : index + 2] for index in range(len(raw) - 1))
    return sorted(terms, key=lambda item: (-len(item), item))


def _score_block(
    block: EvidenceBlock,
    *,
    query: str,
    terms: Sequence[str],
) -> int:
    normalized_query = query.casefold().strip()
    content = block.content.casefold()
    keywords = [item.casefold() for item in block.keywords]
    score = 0
    if normalized_query and normalized_query in content:
        score += 20
    for term in terms:
        if term in content:
            score += 2
        if any(term == keyword for keyword in keywords):
            score += 8
        elif any(term in keyword for keyword in keywords):
            score += 4
    return score
