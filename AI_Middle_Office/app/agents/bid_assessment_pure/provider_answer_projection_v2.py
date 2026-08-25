"""Minimal model-visible Answer contract for Provider Boundary V2.

The Provider supplies business meaning and selects Context evidence.  Runtime
owns canonical block ids, epistemic status, limitation graph links, presentation
hints, Context lineage, and state version.  This keeps mechanical graph-building
out of the model contract without weakening canonical Answer validation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .answer_contracts import (
    AnswerDraft,
    AnswerLimitationCode,
    ClaimType,
    EpistemicStatus,
    LimitationBlock,
    RuntimeFactBlock,
    StatementBlock,
)
from .common import Reference, StrictContract


class ProviderAnswerItemKind(str, Enum):
    """Business-level item kinds the Provider may legitimately choose."""

    FACT = "fact"
    INFERENCE = "inference"
    RECOMMENDATION = "recommendation"
    GENERAL_ADVICE = "general_advice"
    RUNTIME_FACT = "runtime_fact"
    UNCERTAINTY = "uncertainty"


class ProviderAnswerItemProjectionV2(StrictContract):
    """One answer item without Runtime-owned graph or presentation fields."""

    kind: ProviderAnswerItemKind
    text: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "Business answer text only. Do not write citation numbers, page labels, "
            "URLs, file paths, or evidence identifiers inside text."
        ),
    )
    grounding_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=128,
        description=(
            "Context entry refs supporting this item. Required except for "
            "general_advice."
        ),
    )
    basis: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
        description=(
            "Evidence-backed reasoning premise. Required only for inference and "
            "project-specific recommendation."
        ),
    )
    limitation: str | None = Field(
        default=None,
        min_length=1,
        max_length=4_000,
        description=(
            "Why the requested fact cannot currently be established. Required "
            "only for uncertainty."
        ),
    )

    @model_validator(mode="after")
    def validate_item_shape(self) -> "ProviderAnswerItemProjectionV2":
        if len(self.grounding_refs) != len(set(self.grounding_refs)):
            raise ValueError("grounding_refs must be unique")

        requires_basis = self.kind in {
            ProviderAnswerItemKind.INFERENCE,
            ProviderAnswerItemKind.RECOMMENDATION,
        }
        if requires_basis != (self.basis is not None):
            raise ValueError(
                "basis must appear exactly for inference or recommendation"
            )

        is_uncertainty = self.kind is ProviderAnswerItemKind.UNCERTAINTY
        if is_uncertainty != (self.limitation is not None):
            raise ValueError("limitation must appear exactly for uncertainty")
        if is_uncertainty:
            if len(self.grounding_refs) > 64:
                raise ValueError("uncertainty supports at most 64 grounding_refs")
            if self.limitation is not None and (
                len(self.text) + 1 + len(self.limitation) > 4_000
            ):
                raise ValueError(
                    "uncertainty text and limitation must fit the canonical limit"
                )

        if self.kind is ProviderAnswerItemKind.GENERAL_ADVICE:
            if self.grounding_refs:
                raise ValueError("general_advice cannot claim project Grounding")
        elif not self.grounding_refs:
            raise ValueError("non-general answer item requires grounding_refs")
        return self


class ProviderAnswerProjectionV2(StrictContract):
    """Small V2 Answer payload upgraded to canonical blocks by Runtime."""

    response_language: str = Field(
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$",
    )
    items: tuple[ProviderAnswerItemProjectionV2, ...] = Field(
        min_length=1,
        max_length=128,
    )

    def referenced_grounding_refs(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                grounding_ref
                for item in self.items
                for grounding_ref in item.grounding_refs
            )
        )

    def evidence_grounding_refs(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                grounding_ref
                for item in self.items
                if item.kind
                in {
                    ProviderAnswerItemKind.FACT,
                    ProviderAnswerItemKind.INFERENCE,
                    ProviderAnswerItemKind.RECOMMENDATION,
                }
                for grounding_ref in item.grounding_refs
            )
        )

    def limitation_grounding_refs(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                grounding_ref
                for item in self.items
                if item.kind is ProviderAnswerItemKind.UNCERTAINTY
                for grounding_ref in item.grounding_refs
            )
        )

    def runtime_fact_grounding_refs(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                grounding_ref
                for item in self.items
                if item.kind is ProviderAnswerItemKind.RUNTIME_FACT
                for grounding_ref in item.grounding_refs
            )
        )

    def to_canonical(
        self,
        *,
        context_snapshot_ref: str,
        state_version: int,
    ) -> AnswerDraft:
        blocks: list[StatementBlock | RuntimeFactBlock | LimitationBlock] = []
        for index, item in enumerate(self.items, start=1):
            statement_ref = f"answer-v2-item-{index:03d}"
            if item.kind is ProviderAnswerItemKind.FACT:
                blocks.append(
                    StatementBlock(
                        block_id=statement_ref,
                        text=item.text,
                        claim_type=ClaimType.FACT,
                        epistemic_status=EpistemicStatus.SUPPORTED,
                        grounding_refs=item.grounding_refs,
                    )
                )
                continue
            if item.kind is ProviderAnswerItemKind.INFERENCE:
                blocks.append(
                    StatementBlock(
                        block_id=statement_ref,
                        text=item.text,
                        claim_type=ClaimType.INFERENCE,
                        epistemic_status=EpistemicStatus.SUPPORTED,
                        grounding_refs=item.grounding_refs,
                        premise_or_trigger=item.basis,
                    )
                )
                continue
            if item.kind is ProviderAnswerItemKind.RECOMMENDATION:
                blocks.append(
                    StatementBlock(
                        block_id=statement_ref,
                        text=item.text,
                        claim_type=ClaimType.RECOMMENDATION,
                        epistemic_status=EpistemicStatus.SUPPORTED,
                        grounding_refs=item.grounding_refs,
                        premise_or_trigger=item.basis,
                    )
                )
                continue
            if item.kind is ProviderAnswerItemKind.GENERAL_ADVICE:
                blocks.append(
                    StatementBlock(
                        block_id=statement_ref,
                        text=item.text,
                        claim_type=ClaimType.RECOMMENDATION,
                        epistemic_status=EpistemicStatus.SUPPORTED,
                        general_advice=True,
                    )
                )
                continue
            if item.kind is ProviderAnswerItemKind.RUNTIME_FACT:
                blocks.append(
                    RuntimeFactBlock(
                        block_id=statement_ref,
                        text=item.text,
                        grounding_refs=item.grounding_refs,
                    )
                )
                continue

            if item.limitation is None:
                raise ValueError("validated uncertainty item is incomplete")
            limitation_ref = f"answer-v2-limitation-{index:03d}"
            blocks.append(
                LimitationBlock(
                    block_id=limitation_ref,
                    code=AnswerLimitationCode.EVIDENCE_INSUFFICIENT,
                    text=f"{item.text}\n{item.limitation}",
                    grounding_refs=item.grounding_refs,
                    applies_to_statement_refs=(),
                )
            )
        return AnswerDraft(
            response_language=self.response_language,
            blocks=tuple(blocks),
            context_snapshot_ref=context_snapshot_ref,
            state_version=state_version,
        )


def provider_answer_business_rules_v2() -> tuple[str, ...]:
    """Compact guidance mirroring the V2 projection validators."""

    return (
        "只返回 response_language 和 items；不得生成 block_id、状态、限制代码或双向引用",
        "item.text 只写业务内容，不得写引用编号、页码、URL、路径或证据标识；引用由 Runtime 生成",
        "fact、inference、recommendation 只能从 allowed_grounding_refs 选择 citable Evidence Atom",
        "runtime_fact 只能从 allowed_runtime_fact_refs 选择资源身份回执，只能说明资源名称、类型、版本或加载状态",
        "uncertainty 只能从 allowed_limitation_refs 选择 Evidence Atom 或授权资源/限制回执",
        "inference 和 recommendation 必须填写 basis；其他类型不得填写 basis",
        "uncertainty 必须填写 limitation；其他类型不得填写 limitation",
        "闲聊、寒暄、表达转换和无项目事实断言的说明使用 general_advice，且不得携带 grounding_refs",
    )
