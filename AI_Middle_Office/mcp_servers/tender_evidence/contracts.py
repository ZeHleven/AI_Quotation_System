from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResultStatus(str, Enum):
    OK = "ok"
    NO_RESULT = "no_result"
    PARTIAL = "partial"
    FAILED = "failed"


class EvidenceLocator(StrictModel):
    page: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    cell_range: str | None = None
    section: str | None = None
    source_location: str | None = Field(default=None, max_length=500)


class EvidenceRef(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    block_id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=160)
    document_version: int = Field(ge=1)
    locator: EvidenceLocator
    content_hash: str = Field(min_length=8, max_length=128)
    context_read: bool = False
    quote: str | None = Field(default=None, max_length=500)


class EvidenceRefInput(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    block_id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=160)
    document_version: int = Field(ge=1)
    content_hash: str = Field(min_length=8, max_length=128)


class EvidenceStructuralContext(StrictModel):
    relation: Literal[
        "section_parent",
        "table_header_parent",
        "sheet_header_parent",
    ]
    content: str = Field(min_length=1, max_length=2000)
    evidence_ref: EvidenceRef


class DocumentItem(StrictModel):
    document_id: str = Field(min_length=1, max_length=160)
    document_key: str = Field(min_length=1, max_length=160)
    file_name: str = Field(min_length=1, max_length=500)
    document_type: str = Field(min_length=1, max_length=160)
    document_version: int = Field(ge=1)
    sha256: str = Field(min_length=8, max_length=128)
    parse_status: Literal["ready", "partial", "failed"]
    active: bool = True


class DocumentManifest(StrictModel):
    case_id: str = Field(min_length=1, max_length=160)
    manifest_version: int = Field(ge=1)
    manifest_hash: str = Field(min_length=8, max_length=128)
    documents: list[DocumentItem] = Field(default_factory=list)


class EvidenceBlock(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    block_id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=160)
    document_key: str = Field(min_length=1, max_length=160)
    document_version: int = Field(ge=1)
    block_order: int = Field(ge=0)
    locator: EvidenceLocator
    content_hash: str = Field(min_length=8, max_length=128)
    content: str = Field(min_length=1, max_length=50_000)
    keywords: list[str] = Field(default_factory=list)
    structural_context: list[EvidenceStructuralContext] = Field(
        default_factory=list,
        max_length=3,
    )

    @property
    def coverage_content(self) -> str:
        parent_text = "\n".join(
            f"[{item.relation}] {item.content}"
            for item in self.structural_context
        )
        return (
            f"{parent_text}\n[child] {self.content}"
            if parent_text
            else self.content
        )

    def to_ref(self, *, context_read: bool, quote: str | None = None) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=self.evidence_id,
            block_id=self.block_id,
            document_id=self.document_id,
            document_version=self.document_version,
            locator=self.locator,
            content_hash=self.content_hash,
            context_read=context_read,
            quote=quote,
        )


class VersionConflict(StrictModel):
    topic: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)


class TenderCaseDataset(StrictModel):
    case_id: str = Field(min_length=1, max_length=160)
    manifest: DocumentManifest
    blocks: list[EvidenceBlock] = Field(default_factory=list)
    version_conflicts: dict[str, list[VersionConflict]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_case_consistency(self) -> "TenderCaseDataset":
        if self.manifest.case_id != self.case_id:
            raise ValueError("manifest.case_id must equal dataset.case_id")

        documents = {
            (item.document_id, item.document_version): item
            for item in self.manifest.documents
        }
        seen_evidence_ids: set[str] = set()
        for block in self.blocks:
            if block.evidence_id in seen_evidence_ids:
                raise ValueError(f"duplicate evidence_id: {block.evidence_id}")
            seen_evidence_ids.add(block.evidence_id)
            document = documents.get((block.document_id, block.document_version))
            if document is None:
                raise ValueError(
                    "evidence block references a document version absent from manifest: "
                    f"{block.evidence_id}"
                )
            if document.document_key != block.document_key:
                raise ValueError(
                    f"document_key mismatch for evidence block: {block.evidence_id}"
                )
        return self


class TenderEvidenceFile(StrictModel):
    schema_version: Literal["tender_evidence_dataset_v1"]
    cases: list[TenderCaseDataset] = Field(min_length=1)

    @model_validator(mode="after")
    def ensure_unique_cases(self) -> "TenderEvidenceFile":
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("dataset contains duplicate case_id values")
        return self


class ToolEnvelope(StrictModel):
    status: ResultStatus
    data: Any = None
    retryable: bool = False
    trace_id: str = Field(min_length=1)
    error_code: str | None = None
    message: str | None = None
