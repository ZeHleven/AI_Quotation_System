"""Deterministic Citation projection and free Block rendering for B05-2.

The projector consumes only an accepted B05-1 validation decision and a
Runtime-created authority snapshot. The renderer formats already-approved text;
neither component calls a model, Tool, database, network, or fixed workflow.
"""

from __future__ import annotations

import re
from typing import Protocol

from .answer_contracts import (
    AnswerDraft,
    AnswerDraftValidationDecision,
    AnswerLimitationCode,
    ClaimType,
    EpistemicStatus,
    GroundingKind,
    GroundingRecord,
    GroundingSnapshot,
    InteractionBlock,
    LimitationBlock,
    NarrativeBlock,
    PresentationHint,
    SourceBasis,
    StatementBlock,
    StatementSupportRecord,
)
from .answer_runtime import quote_span_hash
from .citation_contracts import (
    CitationAuthorityRecord,
    CitationAuthoritySnapshot,
    CitationBundle,
    CitationIssueCode,
    CitationProjection,
    CitationProjectionDecision,
    CitationProjectionIssue,
    CitationQuoteProjection,
    CitationSourceType,
    RenderedAnswerBlock,
    RenderedAnswerCandidate,
    RenderedCitationLine,
    StatementCitationBinding,
)
from .runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextRepresentation,
)
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import canonical_hash


_MODEL_AUTHORED_CITATION_PATTERN = re.compile(
    r"(?ix)"
    r"(?:https?|file|s3|minio|mcp)://|"
    r"\[[0-9]{1,4}\]|〔[0-9]{1,4}〕|"
    r"\[[^\]\r\n]+\]\([^\)\r\n]+\)|"
    r"\b(?:source|evidence|grounding)_?ref\s*[:=]|"
    r"\b(?:page|p\.)\s*[0-9]{1,6}\b|第\s*[0-9]{1,6}\s*页|"
    r"[A-Za-z]:[\\/]"
)

_UNSAFE_TECHNICAL_OUTPUT_PATTERN = re.compile(
    r"(?i)traceback|stack\s+trace|api[_ -]?key|bearer\s+[A-Za-z0-9._-]+"
)

_SOURCE_TYPE_BY_BASIS = {
    SourceBasis.DOCUMENT: CitationSourceType.DOCUMENT,
    SourceBasis.ENTERPRISE: CitationSourceType.ENTERPRISE_RECORD,
    SourceBasis.BUSINESS_RECORD: CitationSourceType.BUSINESS_RECORD,
    SourceBasis.SYSTEM_RULE: CitationSourceType.SYSTEM_RULE,
    SourceBasis.USER_ASSERTION: CitationSourceType.USER_MESSAGE,
}


class AnswerRenderingRejected(RuntimeError):
    """Safe internal signal; no unvalidated Draft text should be published."""


class _CitationIssueAdder(Protocol):
    def __call__(
        self,
        code: CitationIssueCode,
        message: str,
        *,
        statement_ref: str | None = None,
        grounding_ref: str | None = None,
        quote_ref: str | None = None,
    ) -> None: ...


class CitationProjector:
    """Project Runtime-authoritative source metadata into safe Citations."""

    def project(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        draft: AnswerDraft,
        validation: AnswerDraftValidationDecision,
        grounding_snapshot: GroundingSnapshot,
        authority_snapshot: CitationAuthoritySnapshot,
    ) -> CitationProjectionDecision:
        issues: list[CitationProjectionIssue] = []
        issue_keys: set[tuple[object, ...]] = set()

        def add_issue(
            code: CitationIssueCode,
            message: str,
            *,
            statement_ref: str | None = None,
            grounding_ref: str | None = None,
            quote_ref: str | None = None,
        ) -> None:
            key = (code, statement_ref, grounding_ref, quote_ref)
            if key in issue_keys:
                return
            issue_keys.add(key)
            issues.append(
                CitationProjectionIssue(
                    code=code,
                    message=message,
                    statement_ref=statement_ref,
                    grounding_ref=grounding_ref,
                    quote_ref=quote_ref,
                )
            )

        snapshot = context.snapshot
        draft_hash = canonical_hash(draft)
        draft_ref = f"answer-draft:{draft_hash.removeprefix('sha256:')}"
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.task_id != snapshot.task_ref
            or snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or snapshot.status
            not in {
                ContextAssemblyStatus.READY,
                ContextAssemblyStatus.READY_WITH_LIMITS,
            }
            or not validation.accepted
            or validation.task_ref != snapshot.task_ref
            or validation.context_snapshot_ref != snapshot.snapshot_ref
            or validation.state_version != snapshot.state_version
            or validation.draft_ref != draft_ref
            or validation.draft_hash != draft_hash
            or grounding_snapshot.snapshot_ref
            != validation.grounding_snapshot_ref
            or grounding_snapshot.task_ref != snapshot.task_ref
            or grounding_snapshot.context_snapshot_ref != snapshot.snapshot_ref
            or grounding_snapshot.context_snapshot_hash != snapshot.snapshot_hash
            or authority_snapshot.task_ref != snapshot.task_ref
            or authority_snapshot.state_version != snapshot.state_version
            or authority_snapshot.context_snapshot_ref != snapshot.snapshot_ref
            or authority_snapshot.context_snapshot_hash != snapshot.snapshot_hash
            or authority_snapshot.grounding_snapshot_ref
            != grounding_snapshot.snapshot_ref
            or authority_snapshot.authorization_snapshot_ref
            != snapshot.authorization_snapshot_ref
            or set(authority_snapshot.allowed_scope_refs)
            != set(grounding_snapshot.allowed_scope_refs)
        ):
            add_issue(
                CitationIssueCode.RUNTIME_BINDING_MISMATCH,
                "Draft, validation, Grounding, authority, and Context are not one frozen boundary.",
            )

        for block in draft.blocks:
            if _MODEL_AUTHORED_CITATION_PATTERN.search(block.text):
                add_issue(
                    CitationIssueCode.MODEL_AUTHORED_CITATION,
                    "Draft text contains a model-authored Citation, locator, or transport reference.",
                    statement_ref=(
                        block.block_id if isinstance(block, StatementBlock) else None
                    ),
                )
            if _UNSAFE_TECHNICAL_OUTPUT_PATTERN.search(block.text):
                add_issue(
                    CitationIssueCode.MODEL_AUTHORED_CITATION,
                    "Draft text contains technical output that is not safe for publication.",
                    statement_ref=(
                        block.block_id if isinstance(block, StatementBlock) else None
                    ),
                )

        entry_by_ref = {entry.entry_ref: entry for entry in context.projection_entries}
        grounding_by_ref = {
            record.grounding_ref: record for record in grounding_snapshot.records
        }
        authority_by_ref = {
            record.grounding_ref: record for record in authority_snapshot.records
        }
        support_by_ref = {
            support.statement_ref: support for support in validation.statement_support
        }
        statements = [
            block for block in draft.blocks if isinstance(block, StatementBlock)
        ]
        if set(support_by_ref) != {statement.block_id for statement in statements}:
            add_issue(
                CitationIssueCode.RUNTIME_BINDING_MISMATCH,
                "Statement support receipts do not match the AnswerDraft.",
            )

        statement_grounding_refs: dict[str, tuple[str, ...]] = {}
        quote_refs_by_grounding: dict[str, list[str]] = {}
        statements_by_quote_ref: dict[str, list[StatementBlock]] = {}
        grounding_order: list[str] = []
        validated_grounding_refs = set(validation.validated_grounding_refs)
        validated_quote_refs = set(validation.validated_quote_refs)
        for statement in statements:
            support = support_by_ref.get(statement.block_id)
            if support is None:
                statement_grounding_refs[statement.block_id] = ()
                continue
            eligible_refs: list[str] = []
            if not set(support.grounding_refs).issubset(validated_grounding_refs):
                add_issue(
                    CitationIssueCode.RUNTIME_BINDING_MISMATCH,
                    "Statement support uses Grounding not accepted by the Answer Guard.",
                    statement_ref=statement.block_id,
                )
            for grounding_ref in support.grounding_refs:
                grounding = grounding_by_ref.get(grounding_ref)
                if (
                    grounding_ref in validated_grounding_refs
                    and grounding is not None
                    and grounding.citable
                    and grounding.citation_projection_ready
                    and grounding.source_basis is not SourceBasis.RUNTIME_RECEIPT
                ):
                    eligible_refs.append(grounding_ref)
                    if grounding_ref not in grounding_order:
                        grounding_order.append(grounding_ref)
            if support.citation_required and not eligible_refs:
                add_issue(
                    CitationIssueCode.REQUIRED_CITATION_MISSING,
                    "Material Statement has no eligible Runtime Citation source.",
                    statement_ref=statement.block_id,
                )
            statement_grounding_refs[statement.block_id] = tuple(eligible_refs)
            for quote_ref in statement.quote_refs:
                if quote_ref not in validated_quote_refs:
                    add_issue(
                        CitationIssueCode.RUNTIME_BINDING_MISMATCH,
                        "Statement Quote was not accepted by the Answer Guard.",
                        statement_ref=statement.block_id,
                        quote_ref=quote_ref,
                    )
                    continue
                owner_ref = self._quote_owner_ref(
                    quote_ref=quote_ref,
                    grounding_refs=eligible_refs,
                    grounding_by_ref=grounding_by_ref,
                )
                if owner_ref is None:
                    add_issue(
                        CitationIssueCode.QUOTE_PROJECTION_INVALID,
                        "Quote is not owned by an eligible Citation source.",
                        statement_ref=statement.block_id,
                        quote_ref=quote_ref,
                    )
                    continue
                quote_refs_by_grounding.setdefault(owner_ref, []).append(quote_ref)
                statements_by_quote_ref.setdefault(quote_ref, []).append(statement)

        valid_authorities: dict[str, CitationAuthorityRecord] = {}
        for grounding_ref in grounding_order:
            grounding = grounding_by_ref.get(grounding_ref)
            authority = authority_by_ref.get(grounding_ref)
            if grounding is None:
                add_issue(
                    CitationIssueCode.RUNTIME_BINDING_MISMATCH,
                    "Validated Grounding is missing from its frozen snapshot.",
                    grounding_ref=grounding_ref,
                )
                continue
            if authority is None:
                add_issue(
                    CitationIssueCode.AUTHORITY_RECORD_MISSING,
                    "Citation source is unavailable under the current authorization.",
                    grounding_ref=grounding_ref,
                )
                continue
            if self._authority_is_valid(
                grounding=grounding,
                authority=authority,
                authority_snapshot=authority_snapshot,
                expected_authorization_ref=snapshot.authorization_snapshot_ref,
                add_issue=add_issue,
            ):
                valid_authorities[grounding_ref] = authority

        conflict_group_ordinals: dict[str, int] = {}
        for grounding_ref in grounding_order:
            grounding = grounding_by_ref.get(grounding_ref)
            if grounding is None or grounding.conflict_group_ref is None:
                continue
            conflict_group_ordinals.setdefault(
                grounding.conflict_group_ref,
                len(conflict_group_ordinals) + 1,
            )

        citations: list[CitationProjection] = []
        citation_ref_by_grounding: dict[str, str] = {}
        for grounding_ref in grounding_order:
            grounding = grounding_by_ref.get(grounding_ref)
            authority = valid_authorities.get(grounding_ref)
            if grounding is None or authority is None:
                continue
            quote_projections: list[CitationQuoteProjection] = []
            quote_by_ref = {
                quote.quote_ref: quote for quote in grounding.quote_bindings
            }
            entry = entry_by_ref.get(grounding.context_entry_ref)
            for quote_ref in dict.fromkeys(quote_refs_by_grounding.get(grounding_ref, [])):
                quote = quote_by_ref.get(quote_ref)
                if (
                    quote is None
                    or entry is None
                    or entry.representation is not ContextRepresentation.EXACT
                    or quote.source_projection_hash != entry.projection_hash
                    or quote.end_char > len(entry.content)
                ):
                    add_issue(
                        CitationIssueCode.QUOTE_PROJECTION_INVALID,
                        "Quote cannot be projected from an exact current Context span.",
                        grounding_ref=grounding_ref,
                        quote_ref=quote_ref,
                    )
                    continue
                text = entry.content[quote.start_char : quote.end_char]
                if len(text) > 4_000 or quote_span_hash(text) != quote.quote_hash:
                    add_issue(
                        CitationIssueCode.QUOTE_PROJECTION_INVALID,
                        "Quote span or hash no longer matches the current Context.",
                        grounding_ref=grounding_ref,
                        quote_ref=quote_ref,
                    )
                    continue
                for statement in statements_by_quote_ref.get(quote_ref, []):
                    if text not in statement.text:
                        add_issue(
                            CitationIssueCode.QUOTE_PROJECTION_INVALID,
                            "Direct Quote text does not exactly occur in its Statement.",
                            statement_ref=statement.block_id,
                            grounding_ref=grounding_ref,
                            quote_ref=quote_ref,
                        )
                quote_projections.append(
                    CitationQuoteProjection(
                        quote_ref=quote_ref,
                        text=text,
                        quote_hash=quote.quote_hash,
                    )
                )
            ordinal = len(citations) + 1
            body = {
                "ordinal": ordinal,
                "source_basis": grounding.source_basis.value,
                "source_type": authority.source_type.value,
                "locator_kind": authority.locator_kind.value,
                "safe_title": authority.safe_title,
                "safe_locator_label": authority.safe_locator_label,
                "safe_version_label": authority.safe_version_label,
                "conflict_group_ordinal": (
                    None
                    if grounding.conflict_group_ref is None
                    else conflict_group_ordinals[grounding.conflict_group_ref]
                ),
                "controlled_access_ref": authority.controlled_access_ref,
                "quotes": [quote.model_dump(mode="json") for quote in quote_projections],
            }
            digest = canonical_hash(body)
            citation = CitationProjection(
                **body,
                citation_ref=f"citation:{digest.removeprefix('sha256:')}",
                citation_hash=digest,
            )
            citations.append(citation)
            citation_ref_by_grounding[grounding_ref] = citation.citation_ref

        statement_bindings: list[StatementCitationBinding] = []
        for statement in statements:
            citation_refs = tuple(
                citation_ref_by_grounding[ref]
                for ref in statement_grounding_refs.get(statement.block_id, ())
                if ref in citation_ref_by_grounding
            )
            support = support_by_ref.get(statement.block_id)
            if support is not None and support.citation_required and not citation_refs:
                add_issue(
                    CitationIssueCode.REQUIRED_CITATION_MISSING,
                    "Required Citation could not be projected safely.",
                    statement_ref=statement.block_id,
                )
            if statement.epistemic_status is EpistemicStatus.CONFLICTED:
                cited_groups = {
                    grounding_by_ref[ref].conflict_group_ref
                    for ref in statement_grounding_refs.get(statement.block_id, ())
                    if ref in citation_ref_by_grounding
                    and grounding_by_ref[ref].conflict_group_ref is not None
                }
                if len(cited_groups) < 2:
                    add_issue(
                        CitationIssueCode.CONFLICT_PROJECTION_INCOMPLETE,
                        "Conflicted Statement cannot project every required Source group.",
                        statement_ref=statement.block_id,
                    )
            statement_bindings.append(
                StatementCitationBinding(
                    statement_ref=statement.block_id,
                    citation_refs=citation_refs,
                )
            )

        if issues:
            return CitationProjectionDecision(
                accepted=False,
                task_ref=snapshot.task_ref,
                context_snapshot_ref=snapshot.snapshot_ref,
                draft_ref=draft_ref,
                citation_authority_snapshot_ref=authority_snapshot.snapshot_ref,
                bundle=None,
                issues=tuple(issues),
            )

        bundle_body = {
            "task_ref": snapshot.task_ref,
            "state_version": snapshot.state_version,
            "context_snapshot_ref": snapshot.snapshot_ref,
            "draft_ref": draft_ref,
            "draft_hash": draft_hash,
            "validation_grounding_snapshot_ref": grounding_snapshot.snapshot_ref,
            "citation_authority_snapshot_ref": authority_snapshot.snapshot_ref,
            "authorization_snapshot_ref": snapshot.authorization_snapshot_ref,
            "citations": [citation.model_dump(mode="json") for citation in citations],
            "statement_bindings": [
                binding.model_dump(mode="json") for binding in statement_bindings
            ],
        }
        bundle_hash = canonical_hash(bundle_body)
        bundle = CitationBundle(
            **bundle_body,
            bundle_ref=f"citation-bundle:{bundle_hash.removeprefix('sha256:')}",
            bundle_hash=bundle_hash,
        )
        return CitationProjectionDecision(
            accepted=True,
            task_ref=snapshot.task_ref,
            context_snapshot_ref=snapshot.snapshot_ref,
            draft_ref=draft_ref,
            citation_authority_snapshot_ref=authority_snapshot.snapshot_ref,
            bundle=bundle,
            issues=(),
        )

    @staticmethod
    def _quote_owner_ref(
        *,
        quote_ref: str,
        grounding_refs: list[str],
        grounding_by_ref: dict[str, GroundingRecord],
    ) -> str | None:
        owners = [
            grounding_ref
            for grounding_ref in grounding_refs
            if any(
                binding.quote_ref == quote_ref
                for binding in grounding_by_ref[grounding_ref].quote_bindings
            )
        ]
        return owners[0] if len(owners) == 1 else None

    @staticmethod
    def _expected_source_type(grounding: GroundingRecord) -> CitationSourceType | None:
        if grounding.source_basis is SourceBasis.FORMULA:
            if grounding.grounding_kind is GroundingKind.FORMULA_RULE:
                return CitationSourceType.FORMULA_RULE
            if grounding.grounding_kind is GroundingKind.CALCULATION_RESULT:
                return CitationSourceType.CALCULATION_RESULT
            return None
        return _SOURCE_TYPE_BY_BASIS.get(grounding.source_basis)

    @classmethod
    def _authority_is_valid(
        cls,
        *,
        grounding: GroundingRecord,
        authority: CitationAuthorityRecord,
        authority_snapshot: CitationAuthoritySnapshot,
        expected_authorization_ref: str,
        add_issue: _CitationIssueAdder,
    ) -> bool:
        valid = True

        def reject(code: CitationIssueCode, message: str) -> None:
            nonlocal valid
            valid = False
            add_issue(code, message, grounding_ref=grounding.grounding_ref)

        if not authority.disclosure_allowed:
            reject(
                CitationIssueCode.DISCLOSURE_DENIED,
                "Citation source is unavailable under the current authorization.",
            )
        if (
            authority.authorization_snapshot_ref != expected_authorization_ref
            or authority.authorization_snapshot_ref
            != authority_snapshot.authorization_snapshot_ref
        ):
            reject(
                CitationIssueCode.AUTHORIZATION_MISMATCH,
                "Citation authority does not match the current authorization snapshot.",
            )
        if (
            authority.source_scope_ref != grounding.source_scope_ref
            or authority.source_scope_ref not in authority_snapshot.allowed_scope_refs
        ):
            reject(
                CitationIssueCode.SOURCE_SCOPE_MISMATCH,
                "Citation source is outside the current authorized Scope.",
            )
        if (
            authority.grounding_ref != grounding.grounding_ref
            or authority.source_ref != grounding.source_ref
            or authority.source_version_ref != grounding.source_version_ref
            or authority.source_content_hash != grounding.source_content_hash
            or authority.locator_hash != grounding.locator_hash
            or authority.context_projection_hash != grounding.context_projection_hash
        ):
            reject(
                CitationIssueCode.SOURCE_NOT_CURRENT,
                "Citation authority no longer matches the validated Grounding.",
            )
        if (
            authority.source_head_version_ref != grounding.source_head_version_ref
            or authority.source_head_content_hash != grounding.source_head_content_hash
            or authority.source_head_locator_hash != grounding.source_head_locator_hash
            or authority.source_version_ref != authority.source_head_version_ref
            or authority.source_content_hash != authority.source_head_content_hash
            or authority.locator_hash != authority.source_head_locator_hash
        ):
            reject(
                CitationIssueCode.SOURCE_NOT_CURRENT,
                "Citation source no longer matches its current Source Head.",
            )
        if authority.source_type is not cls._expected_source_type(grounding):
            reject(
                CitationIssueCode.SOURCE_TYPE_MISMATCH,
                "Citation source type is incompatible with the validated Grounding.",
            )
        return valid


class AnswerBlockRenderer:
    """Render approved generic Blocks; never create or revise business claims."""

    max_rendered_bytes = 128 * 1024

    def render(
        self,
        *,
        task: AgentTaskState,
        draft: AnswerDraft,
        validation: AnswerDraftValidationDecision,
        citation_decision: CitationProjectionDecision,
    ) -> RenderedAnswerCandidate:
        bundle = citation_decision.bundle
        draft_hash = canonical_hash(draft)
        if (
            task.status is not AgentTaskStatus.RUNNING
            or not validation.accepted
            or not citation_decision.accepted
            or bundle is None
            or validation.task_ref != task.task_id
            or citation_decision.task_ref != task.task_id
            or citation_decision.context_snapshot_ref
            != validation.context_snapshot_ref
            or citation_decision.draft_ref != validation.draft_ref
            or validation.draft_hash != draft_hash
            or bundle.draft_ref != validation.draft_ref
            or bundle.draft_hash != validation.draft_hash
            or bundle.context_snapshot_ref != validation.context_snapshot_ref
            or bundle.validation_grounding_snapshot_ref
            != validation.grounding_snapshot_ref
            or bundle.citation_authority_snapshot_ref
            != citation_decision.citation_authority_snapshot_ref
        ):
            raise AnswerRenderingRejected(
                "Rendered Answer requires one accepted Draft/Citation boundary"
            )

        citation_by_ref = {
            citation.citation_ref: citation for citation in bundle.citations
        }
        binding_by_ref = {
            binding.statement_ref: binding for binding in bundle.statement_bindings
        }
        support_by_ref = {
            support.statement_ref: support for support in validation.statement_support
        }
        rendered_blocks: list[RenderedAnswerBlock] = []
        language_is_zh = draft.response_language.lower().startswith("zh")
        for block in draft.blocks:
            citation_refs: tuple[str, ...] = ()
            if isinstance(block, NarrativeBlock):
                text = self._present(block.text, block.presentation_hint)
            elif isinstance(block, StatementBlock):
                binding = binding_by_ref.get(block.block_id)
                support = support_by_ref.get(block.block_id)
                if binding is None or support is None:
                    raise AnswerRenderingRejected(
                        "Statement is missing validated Citation/support bindings"
                    )
                citation_refs = binding.citation_refs
                markers = "".join(
                    f"[{citation_by_ref[ref].ordinal}]" for ref in citation_refs
                )
                label = self._statement_label(
                    block=block,
                    support=support,
                    language_is_zh=language_is_zh,
                )
                content = f"{label}{block.text}{markers}"
                text = self._present(content, block.presentation_hint)
            elif isinstance(block, LimitationBlock):
                label = self._limitation_label(block.code, language_is_zh)
                text = f"> {label}{block.text}"
            elif isinstance(block, InteractionBlock):
                text = block.text
            else:  # pragma: no cover - closed Pydantic union
                raise AnswerRenderingRejected("unknown Answer Block type")
            rendered_blocks.append(
                RenderedAnswerBlock(
                    block_ref=block.block_id,
                    block_type=block.block_type,
                    text=text,
                    citation_refs=citation_refs,
                )
            )

        rendered_citations = tuple(
            self._render_citation_line(citation, language_is_zh)
            for citation in bundle.citations
        )
        sections = [block.text for block in rendered_blocks]
        if rendered_citations:
            sections.append("## 依据" if language_is_zh else "## Sources")
            sections.extend(citation.text for citation in rendered_citations)
        text = "\n\n".join(sections)
        if len(text.encode("utf-8")) > self.max_rendered_bytes:
            raise AnswerRenderingRejected("Rendered Answer exceeds Runtime size limit")

        body = {
            "task_ref": task.task_id,
            "state_version": validation.state_version,
            "context_snapshot_ref": validation.context_snapshot_ref,
            "draft_ref": validation.draft_ref,
            "draft_hash": validation.draft_hash,
            "citation_bundle_ref": bundle.bundle_ref,
            "citation_bundle_hash": bundle.bundle_hash,
            "response_language": draft.response_language,
            "text": text,
            "blocks": [block.model_dump(mode="json") for block in rendered_blocks],
            "citations": [
                citation.model_dump(mode="json") for citation in rendered_citations
            ],
        }
        digest = canonical_hash(body)
        return RenderedAnswerCandidate(
            **body,
            rendered_ref=f"rendered-answer:{digest.removeprefix('sha256:')}",
            rendered_hash=digest,
        )

    @staticmethod
    def _present(text: str, hint: PresentationHint) -> str:
        if hint is PresentationHint.HEADING:
            return f"## {text}"
        if hint is PresentationHint.LIST_ITEM:
            return f"- {text}"
        if hint is PresentationHint.CALLOUT:
            return f"> {text}"
        return text

    @staticmethod
    def _statement_label(
        *,
        block: StatementBlock,
        support: StatementSupportRecord,
        language_is_zh: bool,
    ) -> str:
        labels: list[str] = []
        status_labels = (
            {
                EpistemicStatus.PARTIAL: "部分确认",
                EpistemicStatus.CONFLICTED: "存在冲突",
                EpistemicStatus.UNKNOWN: "暂无法确认",
            }
            if language_is_zh
            else {
                EpistemicStatus.PARTIAL: "Partially supported",
                EpistemicStatus.CONFLICTED: "Conflicting evidence",
                EpistemicStatus.UNKNOWN: "Not currently confirmed",
            }
        )
        type_labels = (
            {
                ClaimType.CALCULATION: "测算",
                ClaimType.INFERENCE: "判断",
                ClaimType.RECOMMENDATION: "建议",
            }
            if language_is_zh
            else {
                ClaimType.CALCULATION: "Calculation",
                ClaimType.INFERENCE: "Inference",
                ClaimType.RECOMMENDATION: "Recommendation",
            }
        )
        if block.epistemic_status in status_labels:
            labels.append(status_labels[block.epistemic_status])
        if block.claim_type in type_labels:
            labels.append(type_labels[block.claim_type])
        if support.source_bases == (SourceBasis.USER_ASSERTION,):
            labels.append("用户提供" if language_is_zh else "User-provided")
        if not labels:
            return ""
        separator = "·" if language_is_zh else " / "
        suffix = "：" if language_is_zh else ": "
        return f"【{separator.join(labels)}】{suffix}"

    @staticmethod
    def _limitation_label(
        code: AnswerLimitationCode,
        language_is_zh: bool,
    ) -> str:
        zh = {
            AnswerLimitationCode.RETRIEVAL_NO_RESULT: "检索范围限制",
            AnswerLimitationCode.SOURCE_NOT_PROVIDED: "资料缺失",
            AnswerLimitationCode.EVIDENCE_INSUFFICIENT: "证据不足",
            AnswerLimitationCode.EVIDENCE_CONFLICTED: "证据冲突",
            AnswerLimitationCode.SOURCE_STALE_OR_UNAVAILABLE: "来源当前不可用",
            AnswerLimitationCode.PERMISSION_LIMITED: "访问范围限制",
            AnswerLimitationCode.TOOL_OR_INDEX_DEGRADED: "检索能力受限",
            AnswerLimitationCode.CONTEXT_LIMITED: "回答范围限制",
        }
        en = {
            AnswerLimitationCode.RETRIEVAL_NO_RESULT: "Retrieval scope",
            AnswerLimitationCode.SOURCE_NOT_PROVIDED: "Source not provided",
            AnswerLimitationCode.EVIDENCE_INSUFFICIENT: "Insufficient evidence",
            AnswerLimitationCode.EVIDENCE_CONFLICTED: "Conflicting evidence",
            AnswerLimitationCode.SOURCE_STALE_OR_UNAVAILABLE: "Source unavailable",
            AnswerLimitationCode.PERMISSION_LIMITED: "Access limited",
            AnswerLimitationCode.TOOL_OR_INDEX_DEGRADED: "Retrieval degraded",
            AnswerLimitationCode.CONTEXT_LIMITED: "Context limited",
        }
        label = zh[code] if language_is_zh else en[code]
        return f"【{label}】：" if language_is_zh else f"[{label}]: "

    @staticmethod
    def _render_citation_line(
        citation: CitationProjection,
        language_is_zh: bool,
    ) -> RenderedCitationLine:
        source_labels = (
            {
                CitationSourceType.DOCUMENT: "招标文件",
                CitationSourceType.ENTERPRISE_RECORD: "企业资料",
                CitationSourceType.BUSINESS_RECORD: "业务记录",
                CitationSourceType.SYSTEM_RULE: "系统规则",
                CitationSourceType.USER_MESSAGE: "用户提供",
                CitationSourceType.FORMULA_RULE: "公式/规则",
                CitationSourceType.CALCULATION_RESULT: "计算结果",
            }
            if language_is_zh
            else {
                CitationSourceType.DOCUMENT: "Bid document",
                CitationSourceType.ENTERPRISE_RECORD: "Enterprise record",
                CitationSourceType.BUSINESS_RECORD: "Business record",
                CitationSourceType.SYSTEM_RULE: "System rule",
                CitationSourceType.USER_MESSAGE: "User-provided",
                CitationSourceType.FORMULA_RULE: "Formula/rule",
                CitationSourceType.CALCULATION_RESULT: "Calculation result",
            }
        )
        parts = [
            source_labels[citation.source_type],
            citation.safe_title,
            citation.safe_locator_label,
        ]
        if citation.safe_version_label is not None:
            parts.append(citation.safe_version_label)
        if citation.conflict_group_ordinal is not None:
            parts.append(
                f"冲突来源 {citation.conflict_group_ordinal}"
                if language_is_zh
                else f"Conflict source {citation.conflict_group_ordinal}"
            )
        marker = f"[{citation.ordinal}]"
        return RenderedCitationLine(
            citation_ref=citation.citation_ref,
            ordinal=citation.ordinal,
            marker=marker,
            text=f"{marker} " + " · ".join(parts),
            controlled_access_ref=citation.controlled_access_ref,
        )
