"""Permission-aware Citation and deterministic rendering contracts.

None of these contracts are model-visible. They carry only Runtime-generated
display metadata and opaque controlled-access references; raw storage paths,
transport URLs, credentials, and database identifiers are not Citation fields.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .answer_contracts import SourceBasis
from .common import Reference, StrictContentContract, StrictContract
from .tool_runtime import Sha256Digest, canonical_hash


_UNSAFE_DISPLAY_PATTERN = re.compile(
    r"(?i)(?:https?|file|s3|minio|mcp)://|[A-Za-z]:[\\/]|"
    r"(?:^|[\s(])[/\\](?:data|opt|var|home|users)(?:[/\\]|$)"
)


def validate_safe_citation_display(value: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or "\n" in candidate
        or "\r" in candidate
        or _UNSAFE_DISPLAY_PATTERN.search(candidate)
        or re.search(r"\[[0-9]{1,4}\]|〔[0-9]{1,4}〕", candidate)
        or re.search(r"\[[^\]]+\]\([^\)]+\)", candidate)
    ):
        raise ValueError("Citation display text contains a transport or internal path")
    return candidate


class CitationSourceType(str, Enum):
    DOCUMENT = "document"
    ENTERPRISE_RECORD = "enterprise_record"
    BUSINESS_RECORD = "business_record"
    SYSTEM_RULE = "system_rule"
    USER_MESSAGE = "user_message"
    FORMULA_RULE = "formula_rule"
    CALCULATION_RESULT = "calculation_result"


class CitationLocatorKind(str, Enum):
    PAGE = "page"
    SECTION = "section"
    CLAUSE = "clause"
    TABLE = "table"
    SHEET_CELL = "sheet_cell"
    RECORD = "record"
    MESSAGE = "message"
    RULE = "rule"
    FORMULA = "formula"
    OTHER = "other"


class CitationAuthorityRecord(StrictContract):
    """Runtime-only display authority for exactly one Grounding Record."""

    authority_ref: Reference
    grounding_ref: Reference
    source_ref: Reference
    source_scope_ref: Reference
    authorization_snapshot_ref: Reference
    source_version_ref: Reference
    source_head_version_ref: Reference
    source_content_hash: Sha256Digest
    source_head_content_hash: Sha256Digest
    locator_hash: Sha256Digest
    source_head_locator_hash: Sha256Digest
    context_projection_hash: Sha256Digest
    source_type: CitationSourceType
    locator_kind: CitationLocatorKind
    disclosure_allowed: bool
    safe_title: str | None = Field(default=None, min_length=1, max_length=500)
    safe_locator_label: str | None = Field(default=None, min_length=1, max_length=500)
    safe_version_label: str | None = Field(default=None, min_length=1, max_length=200)
    controlled_access_ref: Reference | None = None

    @field_validator("safe_title", "safe_locator_label", "safe_version_label")
    @classmethod
    def validate_display_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_citation_display(value)

    @model_validator(mode="after")
    def validate_disclosure(self) -> "CitationAuthorityRecord":
        display_values = (
            self.safe_title,
            self.safe_locator_label,
            self.safe_version_label,
            self.controlled_access_ref,
        )
        if self.disclosure_allowed:
            if self.safe_title is None or self.safe_locator_label is None:
                raise ValueError("allowed Citation requires safe title and locator")
        elif any(value is not None for value in display_values):
            raise ValueError("denied Citation authority cannot carry display metadata")
        return self


class CitationAuthoritySnapshot(StrictContract):
    snapshot_ref: Reference
    snapshot_hash: Sha256Digest
    task_ref: Reference
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    context_snapshot_hash: Sha256Digest
    grounding_snapshot_ref: Reference
    authorization_snapshot_ref: Reference
    allowed_scope_refs: tuple[Reference, ...] = Field(min_length=1, max_length=512)
    records: tuple[CitationAuthorityRecord, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )

    @classmethod
    def build(
        cls,
        *,
        task_ref: str,
        state_version: int,
        context_snapshot_ref: str,
        context_snapshot_hash: str,
        grounding_snapshot_ref: str,
        authorization_snapshot_ref: str,
        allowed_scope_refs: tuple[str, ...],
        records: tuple[CitationAuthorityRecord, ...],
    ) -> "CitationAuthoritySnapshot":
        body = {
            "task_ref": task_ref,
            "state_version": state_version,
            "context_snapshot_ref": context_snapshot_ref,
            "context_snapshot_hash": context_snapshot_hash,
            "grounding_snapshot_ref": grounding_snapshot_ref,
            "authorization_snapshot_ref": authorization_snapshot_ref,
            "allowed_scope_refs": list(allowed_scope_refs),
            "records": [record.model_dump(mode="json") for record in records],
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            snapshot_ref=f"citation-authority:{digest.removeprefix('sha256:')}",
            snapshot_hash=digest,
        )

    @model_validator(mode="after")
    def validate_snapshot(self) -> "CitationAuthoritySnapshot":
        if len(self.allowed_scope_refs) != len(set(self.allowed_scope_refs)):
            raise ValueError("Citation authority scopes must be unique")
        grounding_refs = tuple(record.grounding_ref for record in self.records)
        if len(grounding_refs) != len(set(grounding_refs)):
            raise ValueError("Citation authority Grounding refs must be unique")
        body = self.model_dump(
            mode="json",
            exclude={"snapshot_ref", "snapshot_hash"},
        )
        digest = canonical_hash(body)
        if self.snapshot_hash != digest:
            raise ValueError("Citation authority hash does not match its body")
        if self.snapshot_ref != f"citation-authority:{digest.removeprefix('sha256:')}":
            raise ValueError("Citation authority ref does not match its hash")
        return self


class CitationQuoteProjection(StrictContentContract):
    quote_ref: Reference
    text: str = Field(min_length=1, max_length=4_000)
    quote_hash: Sha256Digest


class CitationProjection(StrictContentContract):
    citation_ref: Reference
    citation_hash: Sha256Digest
    ordinal: int = Field(ge=1, le=1000)
    source_basis: SourceBasis
    source_type: CitationSourceType
    locator_kind: CitationLocatorKind
    safe_title: str = Field(min_length=1, max_length=500)
    safe_locator_label: str = Field(min_length=1, max_length=500)
    safe_version_label: str | None = Field(default=None, min_length=1, max_length=200)
    conflict_group_ordinal: int | None = Field(default=None, ge=1, le=1000)
    controlled_access_ref: Reference | None = None
    quotes: tuple[CitationQuoteProjection, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )

    @field_validator("safe_title", "safe_locator_label", "safe_version_label")
    @classmethod
    def validate_display_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_citation_display(value)

    @model_validator(mode="after")
    def validate_projection(self) -> "CitationProjection":
        quote_refs = tuple(quote.quote_ref for quote in self.quotes)
        if len(quote_refs) != len(set(quote_refs)):
            raise ValueError("projected Quote refs must be unique")
        body = self.model_dump(
            mode="json",
            exclude={"citation_ref", "citation_hash"},
        )
        digest = canonical_hash(body)
        if self.citation_hash != digest:
            raise ValueError("Citation hash does not match its projection")
        if self.citation_ref != f"citation:{digest.removeprefix('sha256:')}":
            raise ValueError("Citation ref does not match its hash")
        return self


class StatementCitationBinding(StrictContract):
    statement_ref: Reference
    citation_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)

    @model_validator(mode="after")
    def validate_unique_refs(self) -> "StatementCitationBinding":
        if len(self.citation_refs) != len(set(self.citation_refs)):
            raise ValueError("Statement Citation refs must be unique")
        return self


class CitationBundle(StrictContract):
    bundle_ref: Reference
    bundle_hash: Sha256Digest
    task_ref: Reference
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    draft_ref: Reference
    draft_hash: Sha256Digest
    validation_grounding_snapshot_ref: Reference
    citation_authority_snapshot_ref: Reference
    authorization_snapshot_ref: Reference
    citations: tuple[CitationProjection, ...] = Field(default_factory=tuple, max_length=1000)
    statement_bindings: tuple[StatementCitationBinding, ...] = Field(
        default_factory=tuple,
        max_length=256,
    )

    @model_validator(mode="after")
    def validate_bundle(self) -> "CitationBundle":
        citation_refs = tuple(citation.citation_ref for citation in self.citations)
        if len(citation_refs) != len(set(citation_refs)):
            raise ValueError("Citation refs must be unique")
        if tuple(citation.ordinal for citation in self.citations) != tuple(
            range(1, len(self.citations) + 1)
        ):
            raise ValueError("Citation ordinals must be contiguous")
        statement_refs = tuple(binding.statement_ref for binding in self.statement_bindings)
        if len(statement_refs) != len(set(statement_refs)):
            raise ValueError("Statement Citation bindings must be unique")
        known_citations = set(citation_refs)
        if any(
            not set(binding.citation_refs).issubset(known_citations)
            for binding in self.statement_bindings
        ):
            raise ValueError("Statement binding references an unknown Citation")
        body = self.model_dump(mode="json", exclude={"bundle_ref", "bundle_hash"})
        digest = canonical_hash(body)
        if self.bundle_hash != digest:
            raise ValueError("Citation Bundle hash does not match its body")
        if self.bundle_ref != f"citation-bundle:{digest.removeprefix('sha256:')}":
            raise ValueError("Citation Bundle ref does not match its hash")
        return self


class CitationIssueCode(str, Enum):
    RUNTIME_BINDING_MISMATCH = "runtime_binding_mismatch"
    MODEL_AUTHORED_CITATION = "model_authored_citation"
    AUTHORITY_RECORD_MISSING = "authority_record_missing"
    AUTHORIZATION_MISMATCH = "authorization_mismatch"
    SOURCE_SCOPE_MISMATCH = "source_scope_mismatch"
    SOURCE_NOT_CURRENT = "source_not_current"
    DISCLOSURE_DENIED = "disclosure_denied"
    SOURCE_TYPE_MISMATCH = "source_type_mismatch"
    QUOTE_PROJECTION_INVALID = "quote_projection_invalid"
    REQUIRED_CITATION_MISSING = "required_citation_missing"
    CONFLICT_PROJECTION_INCOMPLETE = "conflict_projection_incomplete"


class CitationProjectionIssue(StrictContract):
    code: CitationIssueCode
    message: str = Field(min_length=1, max_length=500)
    statement_ref: Reference | None = None
    grounding_ref: Reference | None = None
    quote_ref: Reference | None = None


class CitationProjectionDecision(StrictContract):
    accepted: bool
    task_ref: Reference
    context_snapshot_ref: Reference
    draft_ref: Reference
    citation_authority_snapshot_ref: Reference
    bundle: CitationBundle | None = None
    issues: tuple[CitationProjectionIssue, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "CitationProjectionDecision":
        if self.accepted != (self.bundle is not None and not self.issues):
            raise ValueError("accepted Citation decision requires only a valid bundle")
        return self


class RenderedAnswerBlock(StrictContentContract):
    block_ref: Reference
    block_type: Literal[
        "narrative",
        "statement",
        "runtime_fact",
        "limitation",
        "interaction",
    ]
    text: str = Field(min_length=1, max_length=32_000)
    citation_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)


class RenderedCitationLine(StrictContentContract):
    citation_ref: Reference
    ordinal: int = Field(ge=1, le=1000)
    marker: str = Field(min_length=3, max_length=16)
    text: str = Field(min_length=1, max_length=8_000)
    controlled_access_ref: Reference | None = None


class RenderedAnswerCandidate(StrictContentContract):
    rendered_ref: Reference
    rendered_hash: Sha256Digest
    task_ref: Reference
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    draft_ref: Reference
    draft_hash: Sha256Digest
    citation_bundle_ref: Reference
    citation_bundle_hash: Sha256Digest
    response_language: str = Field(min_length=2, max_length=32)
    text: str = Field(min_length=1, max_length=131_072)
    blocks: tuple[RenderedAnswerBlock, ...] = Field(min_length=1, max_length=256)
    citations: tuple[RenderedCitationLine, ...] = Field(default_factory=tuple, max_length=1000)

    @model_validator(mode="after")
    def validate_rendered_candidate(self) -> "RenderedAnswerCandidate":
        block_refs = tuple(block.block_ref for block in self.blocks)
        if len(block_refs) != len(set(block_refs)):
            raise ValueError("Rendered block refs must be unique")
        citation_refs = tuple(citation.citation_ref for citation in self.citations)
        if len(citation_refs) != len(set(citation_refs)):
            raise ValueError("Rendered Citation refs must be unique")
        if tuple(citation.ordinal for citation in self.citations) != tuple(
            range(1, len(self.citations) + 1)
        ):
            raise ValueError("Rendered Citation ordinals must be contiguous")
        if any(
            not set(block.citation_refs).issubset(set(citation_refs))
            for block in self.blocks
        ):
            raise ValueError("Rendered block references an unknown Citation")
        body = self.model_dump(
            mode="json",
            exclude={"rendered_ref", "rendered_hash"},
        )
        digest = canonical_hash(body)
        if self.rendered_hash != digest:
            raise ValueError("Rendered Answer hash does not match its body")
        if self.rendered_ref != f"rendered-answer:{digest.removeprefix('sha256:')}":
            raise ValueError("Rendered Answer ref does not match its hash")
        return self
