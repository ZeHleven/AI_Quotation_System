"""Deterministic B05-1 Grounding Integrity Guard.

This component validates one complete AnswerDraft before publication. It is not
an Agent, model verifier, Tool, renderer, persistence service, or workflow.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from .answer_contracts import (
    AnswerDraft,
    AnswerDraftValidationDecision,
    AnswerLimitationCode,
    ClaimType,
    EpistemicStatus,
    GroundingIssueCode,
    GroundingKind,
    GroundingRecord,
    GroundingSnapshot,
    GroundingStatus,
    GroundingValidationIssue,
    InteractionBlock,
    LimitationBlock,
    SourceBasis,
    StatementBlock,
    StatementSupportRecord,
)
from .runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextProjectionEntry,
    ContextRepresentation,
)
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import canonical_hash


_RECEIPT_KINDS = {
    GroundingKind.RETRIEVAL_RECEIPT,
    GroundingKind.SOURCE_AVAILABILITY_RECEIPT,
    GroundingKind.PERMISSION_RECEIPT,
    GroundingKind.TOOL_RECEIPT,
    GroundingKind.CONTEXT_RECEIPT,
}

_PREMISE_KINDS = {
    GroundingKind.EVIDENCE_ATOM,
    GroundingKind.BUSINESS_RECORD,
    GroundingKind.USER_MESSAGE,
    GroundingKind.SYSTEM_RULE,
}

_LIMITATION_KINDS = {
    AnswerLimitationCode.RETRIEVAL_NO_RESULT: {
        GroundingKind.RETRIEVAL_RECEIPT,
    },
    AnswerLimitationCode.SOURCE_NOT_PROVIDED: {
        GroundingKind.SOURCE_AVAILABILITY_RECEIPT,
    },
    AnswerLimitationCode.EVIDENCE_INSUFFICIENT: {
        *_PREMISE_KINDS,
        GroundingKind.RETRIEVAL_RECEIPT,
        GroundingKind.CONTEXT_RECEIPT,
    },
    AnswerLimitationCode.EVIDENCE_CONFLICTED: _PREMISE_KINDS,
    AnswerLimitationCode.SOURCE_STALE_OR_UNAVAILABLE: {
        GroundingKind.SOURCE_AVAILABILITY_RECEIPT,
    },
    AnswerLimitationCode.PERMISSION_LIMITED: {
        GroundingKind.PERMISSION_RECEIPT,
    },
    AnswerLimitationCode.TOOL_OR_INDEX_DEGRADED: {
        GroundingKind.TOOL_RECEIPT,
    },
    AnswerLimitationCode.CONTEXT_LIMITED: {
        GroundingKind.CONTEXT_RECEIPT,
    },
}

_EXPECTED_SOURCE_BASIS = {
    GroundingKind.EVIDENCE_ATOM: {
        SourceBasis.DOCUMENT,
        SourceBasis.ENTERPRISE,
    },
    GroundingKind.BUSINESS_RECORD: {SourceBasis.BUSINESS_RECORD},
    GroundingKind.USER_MESSAGE: {SourceBasis.USER_ASSERTION},
    GroundingKind.SYSTEM_RULE: {SourceBasis.SYSTEM_RULE},
    GroundingKind.FORMULA_RULE: {SourceBasis.FORMULA},
    GroundingKind.CALCULATION_RESULT: {SourceBasis.FORMULA},
    GroundingKind.RETRIEVAL_RECEIPT: {SourceBasis.RUNTIME_RECEIPT},
    GroundingKind.SOURCE_AVAILABILITY_RECEIPT: {SourceBasis.RUNTIME_RECEIPT},
    GroundingKind.PERMISSION_RECEIPT: {SourceBasis.RUNTIME_RECEIPT},
    GroundingKind.TOOL_RECEIPT: {SourceBasis.RUNTIME_RECEIPT},
    GroundingKind.CONTEXT_RECEIPT: {SourceBasis.RUNTIME_RECEIPT},
}


def quote_span_hash(value: str) -> str:
    """Hash an exact projected quote span without normalizing its text."""

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class _IssueAdder(Protocol):
    def __call__(
        self,
        code: GroundingIssueCode,
        message: str,
        *,
        block_ref: str | None = None,
        grounding_ref: str | None = None,
        quote_ref: str | None = None,
    ) -> None: ...


class GroundingIntegrityGuard:
    """Validate declared evidence responsibility at a single answer boundary."""

    def validate(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        draft: AnswerDraft,
        grounding_snapshot: GroundingSnapshot,
        active_slot_refs: tuple[str, ...] = (),
    ) -> AnswerDraftValidationDecision:
        issues: list[GroundingValidationIssue] = []
        issue_keys: set[tuple[object, ...]] = set()

        def add_issue(
            code: GroundingIssueCode,
            message: str,
            *,
            block_ref: str | None = None,
            grounding_ref: str | None = None,
            quote_ref: str | None = None,
        ) -> None:
            key = (code, block_ref, grounding_ref, quote_ref)
            if key in issue_keys:
                return
            issue_keys.add(key)
            issues.append(
                GroundingValidationIssue(
                    code=code,
                    message=message,
                    block_ref=block_ref,
                    grounding_ref=grounding_ref,
                    quote_ref=quote_ref,
                )
            )

        snapshot = context.snapshot
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.task_id != snapshot.task_ref
            or snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or snapshot.status
            not in {
                ContextAssemblyStatus.READY,
                ContextAssemblyStatus.READY_WITH_LIMITS,
            }
            or draft.context_snapshot_ref != snapshot.snapshot_ref
            or draft.state_version != snapshot.state_version
            or grounding_snapshot.task_ref != snapshot.task_ref
            or grounding_snapshot.state_version != snapshot.state_version
            or grounding_snapshot.context_snapshot_ref != snapshot.snapshot_ref
            or grounding_snapshot.context_snapshot_hash != snapshot.snapshot_hash
            or grounding_snapshot.authorization_snapshot_ref
            != snapshot.authorization_snapshot_ref
        ):
            add_issue(
                GroundingIssueCode.RUNTIME_BINDING_MISMATCH,
                "AnswerDraft, Grounding Snapshot, Task, and Context do not share one active boundary.",
            )

        if len(active_slot_refs) != len(set(active_slot_refs)):
            add_issue(
                GroundingIssueCode.SLOT_REF_INVALID,
                "Active Slot references are not unique.",
            )
        active_slots = set(active_slot_refs)
        for block in draft.blocks:
            if (
                isinstance(block, InteractionBlock)
                and block.slot_ref is not None
                and block.slot_ref not in active_slots
            ):
                add_issue(
                    GroundingIssueCode.SLOT_REF_INVALID,
                    "Interaction references a Slot outside the active runtime boundary.",
                    block_ref=block.block_id,
                )

        entry_by_ref = {entry.entry_ref: entry for entry in context.projection_entries}
        record_by_ref = {
            record.grounding_ref: record for record in grounding_snapshot.records
        }
        selected_refs = draft.referenced_grounding_refs()
        selected_records: dict[str, GroundingRecord] = {}
        for grounding_ref in selected_refs:
            record = record_by_ref.get(grounding_ref)
            if record is None:
                add_issue(
                    GroundingIssueCode.GROUNDING_REF_UNKNOWN,
                    "AnswerDraft selected a Grounding ref outside the frozen Grounding Snapshot.",
                    grounding_ref=grounding_ref,
                )
                continue
            if self._record_is_current(
                record=record,
                grounding_snapshot=grounding_snapshot,
                entry_by_ref=entry_by_ref,
                add_issue=add_issue,
            ):
                selected_records[grounding_ref] = record

        limitation_by_ref = {
            block.block_id: block
            for block in draft.blocks
            if isinstance(block, LimitationBlock)
        }
        for limitation in limitation_by_ref.values():
            allowed_kinds = _LIMITATION_KINDS[limitation.code]
            records = [
                selected_records[ref]
                for ref in limitation.grounding_refs
                if ref in selected_records
            ]
            if not records or any(
                record.grounding_kind not in allowed_kinds for record in records
            ):
                add_issue(
                    GroundingIssueCode.LIMITATION_RECEIPT_INVALID,
                    "Limitation is not backed by a compatible runtime receipt or Grounding record.",
                    block_ref=limitation.block_id,
                )
            if (
                limitation.code is AnswerLimitationCode.EVIDENCE_CONFLICTED
                and not any(
                    record.status is GroundingStatus.CONFLICTED for record in records
                )
            ):
                add_issue(
                    GroundingIssueCode.LIMITATION_RECEIPT_INVALID,
                    "Conflict limitation requires current conflicted Grounding.",
                    block_ref=limitation.block_id,
                )

        statement_support: list[StatementSupportRecord] = []
        validated_quote_refs: list[str] = []
        quote_index = {
            quote.quote_ref: (record, quote)
            for record in grounding_snapshot.records
            for quote in record.quote_bindings
        }
        for statement in (
            block for block in draft.blocks if isinstance(block, StatementBlock)
        ):
            issue_count_before = len(issues)
            records = [
                selected_records[ref]
                for ref in statement.grounding_refs
                if ref in selected_records
            ]
            limitation_codes = {
                limitation_by_ref[ref].code
                for ref in statement.limitation_refs
                if ref in limitation_by_ref
            }
            citation_required, citation_ready = self._validate_support_matrix(
                statement=statement,
                records=records,
                limitation_codes=limitation_codes,
                add_issue=add_issue,
            )
            for quote_ref in statement.quote_refs:
                quote_item = quote_index.get(quote_ref)
                if quote_item is None:
                    add_issue(
                        GroundingIssueCode.QUOTE_REF_UNKNOWN,
                        "Statement selected a Quote ref outside the frozen Grounding Snapshot.",
                        block_ref=statement.block_id,
                        quote_ref=quote_ref,
                    )
                    continue
                record, quote = quote_item
                if record.grounding_ref not in statement.grounding_refs:
                    add_issue(
                        GroundingIssueCode.QUOTE_REF_UNKNOWN,
                        "Quote ref is not owned by a Grounding selected for this Statement.",
                        block_ref=statement.block_id,
                        quote_ref=quote_ref,
                    )
                    continue
                entry = entry_by_ref.get(record.context_entry_ref)
                if (
                    record.grounding_ref not in selected_records
                    or entry is None
                    or not record.citable
                    or not record.citation_projection_ready
                    or entry.representation is not ContextRepresentation.EXACT
                    or quote.source_projection_hash != entry.projection_hash
                    or quote.end_char > len(entry.content)
                    or quote_span_hash(entry.content[quote.start_char : quote.end_char])
                    != quote.quote_hash
                ):
                    add_issue(
                        GroundingIssueCode.QUOTE_SPAN_INVALID,
                        "Quote ref does not match an exact current Context span.",
                        block_ref=statement.block_id,
                        grounding_ref=record.grounding_ref,
                        quote_ref=quote_ref,
                    )
                    continue
                validated_quote_refs.append(quote_ref)

            source_bases = tuple(
                basis
                for basis in SourceBasis
                if any(record.source_basis is basis for record in records)
            )
            statement_support.append(
                StatementSupportRecord(
                    statement_ref=statement.block_id,
                    claim_type=statement.claim_type,
                    epistemic_status=statement.epistemic_status,
                    source_bases=source_bases,
                    grounding_refs=tuple(
                        ref for ref in statement.grounding_refs if ref in selected_records
                    ),
                    quote_refs=tuple(
                        ref for ref in statement.quote_refs if ref in validated_quote_refs
                    ),
                    limitation_refs=statement.limitation_refs,
                    citation_required=citation_required,
                    citation_ready=citation_ready,
                    publishable=len(issues) == issue_count_before,
                )
            )

        draft_hash = canonical_hash(draft)
        draft_ref = f"answer-draft:{draft_hash.removeprefix('sha256:')}"
        limitation_codes = tuple(
            dict.fromkeys(
                block.code
                for block in draft.blocks
                if isinstance(block, LimitationBlock)
            )
        )
        return AnswerDraftValidationDecision(
            accepted=not issues,
            draft_ref=draft_ref,
            draft_hash=draft_hash,
            task_ref=snapshot.task_ref,
            state_version=snapshot.state_version,
            context_snapshot_ref=snapshot.snapshot_ref,
            grounding_snapshot_ref=grounding_snapshot.snapshot_ref,
            statement_support=tuple(statement_support),
            validated_grounding_refs=tuple(
                ref for ref in selected_refs if ref in selected_records
            ),
            validated_quote_refs=tuple(dict.fromkeys(validated_quote_refs)),
            limitation_codes=limitation_codes,
            issues=tuple(issues),
        )

    @staticmethod
    def _record_is_current(
        *,
        record: GroundingRecord,
        grounding_snapshot: GroundingSnapshot,
        entry_by_ref: dict[str, ContextProjectionEntry],
        add_issue: _IssueAdder,
    ) -> bool:
        valid = True

        def reject(code: GroundingIssueCode, message: str) -> None:
            nonlocal valid
            valid = False
            add_issue(code, message, grounding_ref=record.grounding_ref)

        entry = entry_by_ref.get(record.context_entry_ref)
        if entry is None:
            reject(
                GroundingIssueCode.GROUNDING_NOT_IN_CONTEXT,
                "Grounding is not present in the current model Context.",
            )
            return False
        if record.source_scope_ref not in grounding_snapshot.allowed_scope_refs:
            reject(
                GroundingIssueCode.GROUNDING_SCOPE_MISMATCH,
                "Grounding is outside the frozen authorized Scope.",
            )
        if (
            record.authorization_snapshot_ref
            != grounding_snapshot.authorization_snapshot_ref
        ):
            reject(
                GroundingIssueCode.GROUNDING_AUTHORIZATION_MISMATCH,
                "Grounding does not share the current authorization snapshot.",
            )
        if (
            record.source_basis
            not in _EXPECTED_SOURCE_BASIS[record.grounding_kind]
            or record.source_ref != entry.source_ref
            or record.source_version_ref != entry.source_version_ref
            or record.source_content_hash != entry.source_content_hash
            or record.context_projection_hash != entry.projection_hash
        ):
            reject(
                GroundingIssueCode.GROUNDING_SOURCE_MISMATCH,
                "Grounding source metadata does not match the current Context receipt.",
            )
        if (
            record.source_version_ref != record.source_head_version_ref
            or record.source_content_hash != record.source_head_content_hash
            or record.locator_hash != record.source_head_locator_hash
        ):
            reject(
                GroundingIssueCode.GROUNDING_SOURCE_NOT_CURRENT,
                "Grounding no longer matches the current Source Head.",
            )
        if record.status in {
            GroundingStatus.UNSUPPORTED,
            GroundingStatus.STALE,
            GroundingStatus.REVOKED,
        }:
            reject(
                GroundingIssueCode.GROUNDING_STATUS_NOT_PUBLISHABLE,
                "Grounding status cannot support a published answer.",
            )
        return valid

    @staticmethod
    def _validate_support_matrix(
        *,
        statement: StatementBlock,
        records: list[GroundingRecord],
        limitation_codes: set[AnswerLimitationCode],
        add_issue: _IssueAdder,
    ) -> tuple[bool, bool]:
        def issue(code: GroundingIssueCode, message: str) -> None:
            add_issue(code, message, block_ref=statement.block_id)

        general_advice = (
            statement.claim_type is ClaimType.RECOMMENDATION
            and statement.general_advice
        )
        citation_required = (
            statement.epistemic_status is not EpistemicStatus.UNKNOWN
            and not general_advice
        )
        citation_ready = any(
            record.citable and record.citation_projection_ready for record in records
        )

        if statement.epistemic_status is EpistemicStatus.SUPPORTED:
            if not general_advice and (
                not records
                or any(record.status is not GroundingStatus.SUPPORTED for record in records)
            ):
                issue(
                    GroundingIssueCode.SUPPORT_MATRIX_UNSATISFIED,
                    "Supported Statement requires current supported Grounding.",
                )
        elif statement.epistemic_status is EpistemicStatus.PARTIAL:
            if (
                not records
                or not any(
                    record.status
                    in {GroundingStatus.SUPPORTED, GroundingStatus.PARTIAL}
                    for record in records
                )
                or any(
                    record.status
                    not in {GroundingStatus.SUPPORTED, GroundingStatus.PARTIAL}
                    for record in records
                )
            ):
                issue(
                    GroundingIssueCode.SUPPORT_MATRIX_UNSATISFIED,
                    "Partial Statement may use only current supported or partial Grounding.",
                )
        elif statement.epistemic_status is EpistemicStatus.CONFLICTED:
            groups = {
                record.conflict_group_ref
                for record in records
                if record.status is GroundingStatus.CONFLICTED
                and record.conflict_group_ref is not None
            }
            ready_groups = {
                record.conflict_group_ref
                for record in records
                if record.status is GroundingStatus.CONFLICTED
                and record.conflict_group_ref is not None
                and record.citable
                and record.citation_projection_ready
            }
            if len(groups) < 2 or len(ready_groups) < 2:
                issue(
                    GroundingIssueCode.CONFLICT_GROUPS_INSUFFICIENT,
                    "Conflicted Statement requires at least two current citable Source groups.",
                )
            if AnswerLimitationCode.EVIDENCE_CONFLICTED not in limitation_codes:
                issue(
                    GroundingIssueCode.SUPPORT_MATRIX_UNSATISFIED,
                    "Conflicted Statement requires an evidence-conflicted Limitation.",
                )
        else:
            if any(
                record.status is not GroundingStatus.UNKNOWN
                or record.grounding_kind not in _RECEIPT_KINDS
                for record in records
            ):
                issue(
                    GroundingIssueCode.SUPPORT_MATRIX_UNSATISFIED,
                    "Unknown Statement may bind only current coverage or availability receipts.",
                )
            citation_ready = False

        if statement.claim_type is ClaimType.CALCULATION and (
            statement.epistemic_status is not EpistemicStatus.UNKNOWN
        ):
            kinds = {record.grounding_kind for record in records}
            if (
                not (kinds & _PREMISE_KINDS)
                or GroundingKind.FORMULA_RULE not in kinds
                or GroundingKind.CALCULATION_RESULT not in kinds
                or any(record.status is GroundingStatus.UNKNOWN for record in records)
            ):
                issue(
                    GroundingIssueCode.SUPPORT_MATRIX_UNSATISFIED,
                    "Calculation requires verified inputs, Formula/Rule Version, and result receipt.",
                )
        if statement.claim_type is ClaimType.INFERENCE and (
            statement.epistemic_status is not EpistemicStatus.UNKNOWN
        ):
            if not any(
                record.grounding_kind in _PREMISE_KINDS
                and record.citable
                and record.citation_projection_ready
                for record in records
            ):
                issue(
                    GroundingIssueCode.SUPPORT_MATRIX_UNSATISFIED,
                    "Inference requires at least one current citable premise.",
                )
        if (
            statement.claim_type is ClaimType.RECOMMENDATION
            and not general_advice
            and statement.epistemic_status is not EpistemicStatus.UNKNOWN
            and not any(
                record.grounding_kind in _PREMISE_KINDS
                and record.citable
                and record.citation_projection_ready
                for record in records
            )
        ):
            issue(
                GroundingIssueCode.SUPPORT_MATRIX_UNSATISFIED,
                "Project recommendation requires at least one current citable trigger.",
            )
        if general_advice and statement.quote_refs:
            issue(
                GroundingIssueCode.SUPPORT_MATRIX_UNSATISFIED,
                "Unbound general advice cannot claim a direct project quote.",
            )
        if citation_required and not citation_ready:
            issue(
                GroundingIssueCode.CITATION_NOT_READY,
                "Material Statement has no Grounding ready for safe Citation projection.",
            )
        return citation_required, citation_ready
