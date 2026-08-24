"""Immutable canonical registry with four disabled initial tool bindings."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Mapping

from .planning import TaskPlan
from .tools import (
    BidDocumentSearchInput,
    CanonicalToolDefinition,
    DisabledExecution,
    DocumentsOutlineInput,
    DocumentsOutlineOutput,
    EnterpriseKnowledgeSearchInput,
    EvidenceCandidatesOutput,
    EvidenceReadInput,
    EvidenceReadOutput,
    ModelVisibleToolContract,
    ToolSafety,
)


DOCUMENTS_OUTLINE = "documents_outline"
BID_DOCUMENT_SEARCH = "bid_document_search"
ENTERPRISE_KNOWLEDGE_SEARCH = "enterprise_knowledge_search"
EVIDENCE_READ = "evidence_read"

INITIAL_TOOL_NAMES = (
    DOCUMENTS_OUTLINE,
    BID_DOCUMENT_SEARCH,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    EVIDENCE_READ,
)


class ToolRegistryError(LookupError):
    pass


class CanonicalToolRegistry:
    """Small immutable registry; health and permissions belong to Runtime projection."""

    def __init__(self, definitions: Iterable[CanonicalToolDefinition]) -> None:
        indexed: dict[str, CanonicalToolDefinition] = {}
        for definition in definitions:
            if definition.name in indexed:
                raise ValueError(f"duplicate tool name: {definition.name}")
            indexed[definition.name] = definition
        if not indexed:
            raise ValueError("tool registry must contain at least one definition")
        self._definitions: Mapping[str, CanonicalToolDefinition] = MappingProxyType(
            indexed
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def get(self, name: str) -> CanonicalToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise ToolRegistryError(f"unknown tool: {name}") from exc

    def validate_plan(self, plan: TaskPlan) -> None:
        plan.validate_tool_hints(self.names)

    def project_model_contracts(
        self,
        visible_names: Iterable[str],
    ) -> tuple[ModelVisibleToolContract, ...]:
        requested = tuple(visible_names)
        if len(requested) != len(set(requested)):
            raise ValueError("visible tool names must be unique")
        return tuple(self.get(name).model_visible_contract() for name in requested)


def build_initial_registry() -> CanonicalToolRegistry:
    """Build canonical declarations; every default execution binding is fail-closed."""

    safety = ToolSafety(
        effect="read_only",
        data_scope="context_bound",
        external_egress=False,
        requires_approval=False,
    )
    disabled_reason = (
        "Pure Agent tool runtime is disabled; no real execution binding is configured"
    )
    return CanonicalToolRegistry(
        (
            CanonicalToolDefinition(
                name=DOCUMENTS_OUTLINE,
                description=(
                    "读取当前招标资料中指定文档的章节层级、页码范围和结构导航信息。"
                    "适用于长文档首次导航、定位可能相关的章节、判断目录结构或缩小后续检索范围；"
                    "需要具体条款原文时继续使用 bid_document_search。"
                ),
                input_model=DocumentsOutlineInput,
                output_model=DocumentsOutlineOutput,
                execution=DisabledExecution(
                    binding_id="disabled.documents_outline",
                    reason=disabled_reason,
                ),
                safety=safety,
            ),
            CanonicalToolDefinition(
                name=BID_DOCUMENT_SEARCH,
                description=(
                    "在当前会话已绑定的招标资料中检索原文候选，返回排序后的 evidence_ref、"
                    "简短片段和文档定位。适用于查找招标条件、关键日期、资格要求、否决条款、"
                    "保证金、费用及评分规则；搜索结果只用于定位，引用或形成事实前必须调用 evidence_read。"
                ),
                input_model=BidDocumentSearchInput,
                output_model=EvidenceCandidatesOutput,
                execution=DisabledExecution(
                    binding_id="disabled.bid_document_search",
                    reason=disabled_reason,
                ),
                safety=safety,
            ),
            CanonicalToolDefinition(
                name=ENTERPRISE_KNOWLEDGE_SEARCH,
                description=(
                    "在当前获授权企业范围的离线知识库中检索企业资料候选，返回排序后的 evidence_ref、"
                    "简短片段和来源定位。适用于查找企业资质、人员证书、项目业绩、产能、财务能力和客户历史，"
                    "以回答企业能力或招标匹配问题；引用或形成事实前必须调用 evidence_read。"
                ),
                input_model=EnterpriseKnowledgeSearchInput,
                output_model=EvidenceCandidatesOutput,
                execution=DisabledExecution(
                    binding_id="disabled.enterprise_knowledge_search",
                    reason=disabled_reason,
                ),
                safety=safety,
            ),
            CanonicalToolDefinition(
                name=EVIDENCE_READ,
                description=(
                    "按一个或多个已有 evidence_ref 读取已授权来源中的原文、页码或章节定位及有限上下文，"
                    "返回可引用的证据内容。适用于核实搜索候选，以及在引用、提取事实、比较要求或判断风险前"
                    "取得原文；内容发现应先使用对应 Search Tool。"
                ),
                input_model=EvidenceReadInput,
                output_model=EvidenceReadOutput,
                execution=DisabledExecution(
                    binding_id="disabled.evidence_read",
                    reason=disabled_reason,
                ),
                safety=safety,
            ),
        )
    )
