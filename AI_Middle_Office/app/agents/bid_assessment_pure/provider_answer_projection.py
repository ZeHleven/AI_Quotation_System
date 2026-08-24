"""Compact model-visible answer projection with Runtime-authoritative binding.

The provider writes only the business answer fields it can legitimately decide.
The Runtime injects Context/version lineage and validates the resulting canonical
``MainAgentModelDecision`` and ``AnswerDraft`` contracts before they gain any
authority.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .action_runtime import (
    AnswerAction,
    InformationRequestAction,
    MainAgentModelActionKind,
    MainAgentModelDecision,
    PlanActionRequest,
)
from .answer_contracts import (
    AnswerDraft,
    AnswerLimitationCode,
    ClaimType,
    EpistemicStatus,
    InteractionBlock,
    LimitationBlock,
    NarrativeBlock,
    PresentationHint,
    StatementBlock,
)
from .common import Reference, StrictContract


class ProviderAnswerBlockProjection(StrictContract):
    """One flat model-visible block; ``block_type`` selects valid fields."""

    block_type: Literal["narrative", "statement", "limitation", "interaction"]
    block_id: Reference
    text: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "Business answer text only. Do not write citation numbers, page/locator "
            "labels, URLs, file paths, or source/evidence/grounding ref identifiers "
            "inside text; select grounding_refs and let Runtime project citations."
        ),
    )
    presentation_hint: PresentationHint | None = None
    claim_type: ClaimType | None = None
    epistemic_status: EpistemicStatus | None = None
    grounding_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=128,
        description="Evidence refs from the current Context that support this block.",
    )
    premise_or_trigger: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
        description=(
            "Required for inference and for project-specific recommendation; state "
            "the evidence-backed premise or trigger."
        ),
    )
    limitation_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=32,
        description=(
            "Required for partial, conflicted, or unknown statements; each ref must "
            "point to a reciprocal limitation block."
        ),
    )
    general_advice: bool = Field(
        default=False,
        description="True only for a non-project-specific recommendation.",
    )
    code: AnswerLimitationCode | None = None
    applies_to_statement_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    slot_ref: Reference | None = None

    @model_validator(mode="after")
    def validate_block_projection(self) -> "ProviderAnswerBlockProjection":
        for field_name in (
            "grounding_refs",
            "limitation_refs",
            "applies_to_statement_refs",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")

        if self.block_type == "narrative":
            self._reject_fields(
                claim_type=self.claim_type,
                epistemic_status=self.epistemic_status,
                grounding_refs=self.grounding_refs,
                premise_or_trigger=self.premise_or_trigger,
                limitation_refs=self.limitation_refs,
                general_advice=self.general_advice,
                code=self.code,
                applies_to_statement_refs=self.applies_to_statement_refs,
                slot_ref=self.slot_ref,
            )
        elif self.block_type == "statement":
            if self.claim_type is None or self.epistemic_status is None:
                raise ValueError("statement requires claim_type and epistemic_status")
            if self.general_advice and self.claim_type is not ClaimType.RECOMMENDATION:
                raise ValueError("general_advice is only valid for a recommendation")
            if (
                self.claim_type is ClaimType.INFERENCE
                and self.premise_or_trigger is None
            ):
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
            self._reject_fields(
                code=self.code,
                applies_to_statement_refs=self.applies_to_statement_refs,
                slot_ref=self.slot_ref,
            )
        elif self.block_type == "limitation":
            if self.code is None or not self.grounding_refs:
                raise ValueError("limitation requires code and grounding_refs")
            self._reject_fields(
                presentation_hint=self.presentation_hint,
                claim_type=self.claim_type,
                epistemic_status=self.epistemic_status,
                premise_or_trigger=self.premise_or_trigger,
                limitation_refs=self.limitation_refs,
                general_advice=self.general_advice,
                slot_ref=self.slot_ref,
            )
        else:
            self._reject_fields(
                presentation_hint=self.presentation_hint,
                claim_type=self.claim_type,
                epistemic_status=self.epistemic_status,
                grounding_refs=self.grounding_refs,
                premise_or_trigger=self.premise_or_trigger,
                limitation_refs=self.limitation_refs,
                general_advice=self.general_advice,
                code=self.code,
                applies_to_statement_refs=self.applies_to_statement_refs,
            )
        return self

    @staticmethod
    def _reject_fields(**values: object) -> None:
        populated = [
            name
            for name, value in values.items()
            if value not in (None, False, (), [])
        ]
        if populated:
            raise ValueError(
                "fields are not valid for block_type: " + ", ".join(populated)
            )

    def to_canonical(self) -> (
        NarrativeBlock | StatementBlock | LimitationBlock | InteractionBlock
    ):
        if self.block_type == "narrative":
            return NarrativeBlock(
                block_id=self.block_id,
                text=self.text,
                presentation_hint=self.presentation_hint or PresentationHint.PARAGRAPH,
            )
        if self.block_type == "statement":
            if self.claim_type is None or self.epistemic_status is None:
                raise ValueError("validated statement projection is incomplete")
            return StatementBlock(
                block_id=self.block_id,
                text=self.text,
                presentation_hint=self.presentation_hint or PresentationHint.PARAGRAPH,
                claim_type=self.claim_type,
                epistemic_status=self.epistemic_status,
                grounding_refs=self.grounding_refs,
                premise_or_trigger=self.premise_or_trigger,
                quote_refs=(),
                limitation_refs=self.limitation_refs,
                general_advice=self.general_advice,
            )
        if self.block_type == "limitation":
            if self.code is None:
                raise ValueError("validated limitation projection is incomplete")
            return LimitationBlock(
                block_id=self.block_id,
                code=self.code,
                text=self.text,
                grounding_refs=self.grounding_refs,
                applies_to_statement_refs=self.applies_to_statement_refs,
            )
        return InteractionBlock(
            block_id=self.block_id,
            text=self.text,
            slot_ref=self.slot_ref,
        )


class ProviderAnswerProjection(StrictContract):
    """Free-form answer content visible to the provider, without Runtime lineage."""

    response_language: str = Field(
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$",
    )
    blocks: tuple[ProviderAnswerBlockProjection, ...] = Field(
        min_length=1,
        max_length=256,
    )

    def to_canonical(
        self,
        *,
        context_snapshot_ref: str,
        state_version: int,
    ) -> AnswerDraft:
        return AnswerDraft(
            response_language=self.response_language,
            blocks=tuple(block.to_canonical() for block in self.blocks),
            context_snapshot_ref=context_snapshot_ref,
            state_version=state_version,
        )


class ProviderDecisionProjection(StrictContract):
    """Small common envelope; action-specific payload is validated by Runtime."""

    action_kind: MainAgentModelActionKind
    concise_basis: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any]


def project_provider_decision(
    projection: ProviderDecisionProjection,
    *,
    context_snapshot_ref: str,
    state_version: int,
) -> MainAgentModelDecision:
    """Upgrade an untrusted provider projection to canonical Runtime contracts."""

    common = {
        "action_kind": projection.action_kind,
        "concise_basis": projection.concise_basis,
    }
    if projection.action_kind in {
        MainAgentModelActionKind.PLAN,
        MainAgentModelActionKind.REPLAN,
    }:
        return MainAgentModelDecision(
            **common,
            plan_request=PlanActionRequest.model_validate(projection.payload),
        )
    if projection.action_kind is MainAgentModelActionKind.REQUEST_INFORMATION:
        return MainAgentModelDecision(
            **common,
            information_request=InformationRequestAction.model_validate(
                projection.payload
            ),
        )

    answer_projection = ProviderAnswerProjection.model_validate(projection.payload)
    return MainAgentModelDecision(
        **common,
        answer=AnswerAction(
            draft=answer_projection.to_canonical(
                context_snapshot_ref=context_snapshot_ref,
                state_version=state_version,
            )
        ),
    )


def provider_action_payload_schemas() -> dict[str, dict[str, Any]]:
    """Guidance schemas; only the compact envelope is provider-enforced."""

    return {
        "plan_or_replan": PlanActionRequest.model_json_schema(),
        "request_information": InformationRequestAction.model_json_schema(),
        "answer": ProviderAnswerProjection.model_json_schema(),
    }


def provider_answer_business_rules() -> tuple[str, ...]:
    """Small model guidance mirroring existing Runtime validators."""

    return (
        "所有 block.text 只写业务内容，不得写 [1]、第N页/page N、URL、文件路径或 "
        "source/evidence/grounding ref；只在 grounding_refs 选择当前 Context entry_ref，"
        "最终引用编号与定位信息由 Runtime CitationProjector 自动生成",
        "inference 必须填写 premise_or_trigger，说明由哪些证据前提推导",
        "项目相关 recommendation 必须填写 premise_or_trigger；仅通用建议可设 general_advice=true",
        "partial、conflicted、unknown statement 必须引用 limitation_refs",
        "statement.limitation_refs 与 limitation.applies_to_statement_refs 必须双向互引",
        "limitation 必须填写 code 和至少一个当前 Context grounding_refs",
        "narrative/interaction 不得携带 statement 或 limitation 专属字段",
    )
