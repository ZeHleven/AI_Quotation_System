"""Free-form AnswerDraft and runtime-authoritative grounding contracts.

The contracts structure evidence responsibility, not the user's business
question or the rendered answer. They have no external side effects.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .common import Reference, StrictContract
from .tool_runtime import Sha256Digest, canonical_hash


class PresentationHint(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE_CELL = "table_cell"
    CALLOUT = "callout"


class ClaimType(str, Enum):
    FACT = "fact"
    CALCULATION = "calculation"
    INFERENCE = "inference"
    RECOMMENDATION = "recommendation"


class EpistemicStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"


class SourceBasis(str, Enum):
    DOCUMENT = "document"
    ENTERPRISE = "enterprise"
    BUSINESS_RECORD = "business_record"
    SYSTEM_RULE = "system_rule"
    USER_ASSERTION = "user_assertion"
    FORMULA = "formula"
    RUNTIME_RECEIPT = "runtime_receipt"


class GroundingKind(str, Enum):
    EVIDENCE_ATOM = "evidence_atom"
    BUSINESS_RECORD = "business_record"
    USER_MESSAGE = "user_message"
    SYSTEM_RULE = "system_rule"
    FORMULA_RULE = "formula_rule"
    CALCULATION_RESULT = "calculation_result"
    RETRIEVAL_RECEIPT = "retrieval_receipt"
    SOURCE_AVAILABILITY_RECEIPT = "source_availability_receipt"
    PERMISSION_RECEIPT = "permission_receipt"
    TOOL_RECEIPT = "tool_receipt"
    CONTEXT_RECEIPT = "context_receipt"


class GroundingStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    STALE = "stale"
    REVOKED = "revoked"


class AnswerLimitationCode(str, Enum):
    RETRIEVAL_NO_RESULT = "retrieval_no_result"
    SOURCE_NOT_PROVIDED = "source_not_provided"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    EVIDENCE_CONFLICTED = "evidence_conflicted"
    SOURCE_STALE_OR_UNAVAILABLE = "source_stale_or_unavailable"
    PERMISSION_LIMITED = "permission_limited"
    TOOL_OR_INDEX_DEGRADED = "tool_or_index_degraded"
    CONTEXT_LIMITED = "context_limited"


class NarrativeBlock(StrictContract):
    block_type: Literal["narrative"] = "narrative"
    block_id: Reference
    text: str = Field(min_length=1, max_length=20_000)
    presentation_hint: PresentationHint = PresentationHint.PARAGRAPH


class StatementBlock(StrictContract):
    block_type: Literal["statement"] = "statement"
    block_id: Reference
    text: str = Field(min_length=1, max_length=20_000)
    presentation_hint: PresentationHint = PresentationHint.PARAGRAPH
    claim_type: ClaimType
    epistemic_status: EpistemicStatus
    grounding_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    premise_or_trigger: str | None = Field(default=None, min_length=1, max_length=2_000)
    quote_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=64)
    limitation_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=32)
    general_advice: bool = False

    @model_validator(mode="after")
    def validate_statement_shape(self) -> "StatementBlock":
        for field_name in ("grounding_refs", "quote_refs", "limitation_refs"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if self.general_advice and self.claim_type is not ClaimType.RECOMMENDATION:
            raise ValueError("general_advice is only valid for a recommendation")
        if self.claim_type is ClaimType.INFERENCE and self.premise_or_trigger is None:
            raise ValueError("inference requires premise_or_trigger")
        if (
            self.claim_type is ClaimType.RECOMMENDATION
            and not self.general_advice
            and self.premise_or_trigger is None
        ):
            raise ValueError("project recommendation requires premise_or_trigger")
        if self.epistemic_status in {
            EpistemicStatus.PARTIAL,
            EpistemicStatus.CONFLICTED,
            EpistemicStatus.UNKNOWN,
        } and not self.limitation_refs:
            raise ValueError("non-supported statement requires limitation_refs")
        if self.epistemic_status is EpistemicStatus.UNKNOWN and self.quote_refs:
            raise ValueError("unknown statement cannot claim a direct quote")
        return self


class LimitationBlock(StrictContract):
    block_type: Literal["limitation"] = "limitation"
    block_id: Reference
    code: AnswerLimitationCode
    text: str = Field(min_length=1, max_length=4_000)
    grounding_refs: tuple[Reference, ...] = Field(min_length=1, max_length=64)
    applies_to_statement_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_unique_refs(self) -> "LimitationBlock":
        for field_name in ("grounding_refs", "applies_to_statement_refs"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class InteractionBlock(StrictContract):
    block_type: Literal["interaction"] = "interaction"
    block_id: Reference
    text: str = Field(min_length=1, max_length=4_000)
    slot_ref: Reference | None = None


AnswerBlock = Annotated[
    Union[NarrativeBlock, StatementBlock, LimitationBlock, InteractionBlock],
    Field(discriminator="block_type"),
]


class AnswerDraft(StrictContract):
    """Provider-visible free answer with runtime-verifiable evidence bindings."""

    schema_name: Literal["bid.answer.draft.v1"] = "bid.answer.draft.v1"
    response_language: str = Field(
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$",
    )
    blocks: tuple[AnswerBlock, ...] = Field(min_length=1, max_length=256)
    context_snapshot_ref: Reference
    state_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_block_graph(self) -> "AnswerDraft":
        block_refs = tuple(block.block_id for block in self.blocks)
        if len(block_refs) != len(set(block_refs)):
            raise ValueError("AnswerDraft block ids must be unique")
        statements = {
            block.block_id: block
            for block in self.blocks
            if isinstance(block, StatementBlock)
        }
        limitations = {
            block.block_id: block
            for block in self.blocks
            if isinstance(block, LimitationBlock)
        }
        for statement in statements.values():
            for limitation_ref in statement.limitation_refs:
                limitation = limitations.get(limitation_ref)
                if limitation is None:
                    raise ValueError("statement references an unknown limitation block")
                if statement.block_id not in limitation.applies_to_statement_refs:
                    raise ValueError("statement/limitation links must be reciprocal")
        for limitation in limitations.values():
            for statement_ref in limitation.applies_to_statement_refs:
                statement = statements.get(statement_ref)
                if statement is None:
                    raise ValueError("limitation references an unknown statement block")
                if limitation.block_id not in statement.limitation_refs:
                    raise ValueError("limitation/statement links must be reciprocal")
        return self

    def referenced_grounding_refs(self) -> tuple[str, ...]:
        refs: list[str] = []
        for block in self.blocks:
            if isinstance(block, (StatementBlock, LimitationBlock)):
                refs.extend(block.grounding_refs)
        return tuple(dict.fromkeys(refs))

    def referenced_quote_refs(self) -> tuple[str, ...]:
        refs = [
            quote_ref
            for block in self.blocks
            if isinstance(block, StatementBlock)
            for quote_ref in block.quote_refs
        ]
        return tuple(dict.fromkeys(refs))

    def referenced_slot_refs(self) -> tuple[str, ...]:
        refs = [
            block.slot_ref
            for block in self.blocks
            if isinstance(block, InteractionBlock) and block.slot_ref is not None
        ]
        return tuple(dict.fromkeys(refs))


class GroundingQuoteBinding(StrictContract):
    """Runtime-created exact span; the model selects only quote_ref."""

    quote_ref: Reference
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=1)
    quote_hash: Sha256Digest
    source_projection_hash: Sha256Digest

    @model_validator(mode="after")
    def validate_span(self) -> "GroundingQuoteBinding":
        if self.end_char <= self.start_char:
            raise ValueError("quote span must be non-empty")
        return self


class GroundingRecord(StrictContract):
    """Runtime authority record; never accepted from model-written citation data."""

    grounding_ref: Reference
    context_entry_ref: Reference
    source_ref: Reference
    source_basis: SourceBasis
    grounding_kind: GroundingKind
    source_scope_ref: Reference
    authorization_snapshot_ref: Reference
    source_version_ref: Reference
    source_head_version_ref: Reference
    source_content_hash: Sha256Digest
    source_head_content_hash: Sha256Digest
    locator_hash: Sha256Digest
    source_head_locator_hash: Sha256Digest
    context_projection_hash: Sha256Digest
    status: GroundingStatus
    citable: bool
    citation_projection_ready: bool
    conflict_group_ref: Reference | None = None
    quote_bindings: tuple[GroundingQuoteBinding, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_record_shape(self) -> "GroundingRecord":
        if self.grounding_ref != self.context_entry_ref:
            raise ValueError("grounding_ref must be the model-visible Context entry ref")
        if self.citation_projection_ready and not self.citable:
            raise ValueError("citation projection cannot be ready for non-citable data")
        quote_refs = tuple(binding.quote_ref for binding in self.quote_bindings)
        if len(quote_refs) != len(set(quote_refs)):
            raise ValueError("quote refs must be unique within Grounding")
        if (
            self.status is GroundingStatus.CONFLICTED
            and self.conflict_group_ref is None
        ):
            raise ValueError("conflicted Grounding requires conflict_group_ref")
        return self


class GroundingSnapshot(StrictContract):
    """Immutable runtime receipt for Grounding visible in one Context Snapshot."""

    snapshot_ref: Reference
    snapshot_hash: Sha256Digest
    task_ref: Reference
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    context_snapshot_hash: Sha256Digest
    authorization_snapshot_ref: Reference
    allowed_scope_refs: tuple[Reference, ...] = Field(min_length=1, max_length=512)
    records: tuple[GroundingRecord, ...] = Field(default_factory=tuple, max_length=1000)

    @classmethod
    def build(
        cls,
        *,
        task_ref: str,
        state_version: int,
        context_snapshot_ref: str,
        context_snapshot_hash: str,
        authorization_snapshot_ref: str,
        allowed_scope_refs: tuple[str, ...],
        records: tuple[GroundingRecord, ...],
    ) -> "GroundingSnapshot":
        body = {
            "task_ref": task_ref,
            "state_version": state_version,
            "context_snapshot_ref": context_snapshot_ref,
            "context_snapshot_hash": context_snapshot_hash,
            "authorization_snapshot_ref": authorization_snapshot_ref,
            "allowed_scope_refs": list(allowed_scope_refs),
            "records": [record.model_dump(mode="json") for record in records],
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            snapshot_ref=f"grounding-snapshot:{digest.removeprefix('sha256:')}",
            snapshot_hash=digest,
        )

    @model_validator(mode="after")
    def validate_snapshot(self) -> "GroundingSnapshot":
        if len(self.allowed_scope_refs) != len(set(self.allowed_scope_refs)):
            raise ValueError("allowed Grounding scopes must be unique")
        grounding_refs = tuple(record.grounding_ref for record in self.records)
        if len(grounding_refs) != len(set(grounding_refs)):
            raise ValueError("Grounding refs must be unique")
        quote_refs = [
            quote.quote_ref
            for record in self.records
            for quote in record.quote_bindings
        ]
        if len(quote_refs) != len(set(quote_refs)):
            raise ValueError("Quote refs must be unique across Grounding Snapshot")
        body = self.model_dump(
            mode="json",
            exclude={"snapshot_ref", "snapshot_hash"},
        )
        digest = canonical_hash(body)
        if self.snapshot_hash != digest:
            raise ValueError("Grounding Snapshot hash does not match its body")
        if self.snapshot_ref != f"grounding-snapshot:{digest.removeprefix('sha256:')}":
            raise ValueError("Grounding Snapshot ref does not match its hash")
        return self


class GroundingIssueCode(str, Enum):
    RUNTIME_BINDING_MISMATCH = "runtime_binding_mismatch"
    GROUNDING_REF_UNKNOWN = "grounding_ref_unknown"
    GROUNDING_NOT_IN_CONTEXT = "grounding_not_in_context"
    GROUNDING_SCOPE_MISMATCH = "grounding_scope_mismatch"
    GROUNDING_AUTHORIZATION_MISMATCH = "grounding_authorization_mismatch"
    GROUNDING_SOURCE_MISMATCH = "grounding_source_mismatch"
    GROUNDING_SOURCE_NOT_CURRENT = "grounding_source_not_current"
    GROUNDING_STATUS_NOT_PUBLISHABLE = "grounding_status_not_publishable"
    SUPPORT_MATRIX_UNSATISFIED = "support_matrix_unsatisfied"
    CITATION_NOT_READY = "citation_not_ready"
    LIMITATION_RECEIPT_INVALID = "limitation_receipt_invalid"
    CONFLICT_GROUPS_INSUFFICIENT = "conflict_groups_insufficient"
    QUOTE_REF_UNKNOWN = "quote_ref_unknown"
    QUOTE_SPAN_INVALID = "quote_span_invalid"
    SLOT_REF_INVALID = "slot_ref_invalid"


class GroundingValidationIssue(StrictContract):
    code: GroundingIssueCode
    message: str = Field(min_length=1, max_length=500)
    block_ref: Reference | None = None
    grounding_ref: Reference | None = None
    quote_ref: Reference | None = None


class StatementSupportRecord(StrictContract):
    statement_ref: Reference
    claim_type: ClaimType
    epistemic_status: EpistemicStatus
    source_bases: tuple[SourceBasis, ...] = Field(default_factory=tuple, max_length=16)
    grounding_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    quote_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=64)
    limitation_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=32)
    citation_required: bool
    citation_ready: bool
    publishable: bool


class AnswerDraftValidationDecision(StrictContract):
    accepted: bool
    draft_ref: Reference
    draft_hash: Sha256Digest
    task_ref: Reference
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    grounding_snapshot_ref: Reference
    statement_support: tuple[StatementSupportRecord, ...] = Field(
        default_factory=tuple,
        max_length=256,
    )
    validated_grounding_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )
    validated_quote_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )
    limitation_codes: tuple[AnswerLimitationCode, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    issues: tuple[GroundingValidationIssue, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "AnswerDraftValidationDecision":
        if self.accepted != (not self.issues):
            raise ValueError("accepted must be true exactly when no issues remain")
        if self.accepted and any(not item.publishable for item in self.statement_support):
            raise ValueError("accepted AnswerDraft cannot contain rejected statements")
        return self
