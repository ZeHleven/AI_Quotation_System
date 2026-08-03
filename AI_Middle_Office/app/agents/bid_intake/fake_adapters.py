from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage

from .contracts import (
    AssessmentDraft,
    ConflictItem,
    DimensionReview,
    DimensionStatus,
    DocumentManifest,
    EvidenceLocator,
    EvidenceRef,
    Finding,
    PolicyFactorInput,
    PolicyFactorRating,
    PolicyFactorSource,
    ProjectFact,
    Recommendation,
    REQUIRED_DIMENSIONS,
    Severity,
    ToolResult,
    ToolResultStatus,
)


@dataclass(frozen=True)
class FakeEvidenceRecord:
    evidence_id: str
    block_id: str
    document_id: str
    document_version: int
    content_hash: str
    content: str
    locator: EvidenceLocator
    search_terms: tuple[str, ...] = ()

    def ref(self, *, context_read: bool = False, quote: str | None = None) -> EvidenceRef:
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


@dataclass
class FakeTenderEvidenceClient:
    """Project-scoped in-memory adapter used by tests and the demo CLI."""

    case_id: str
    records: dict[str, FakeEvidenceRecord]
    version_comparisons: dict[str, dict[str, Any]] = field(default_factory=dict)
    read_evidence_ids: set[str] = field(default_factory=set)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def _trace_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid4()}"

    def search(self, *, query: str, top_k: int) -> ToolResult:
        self.calls.append({"tool": "search_tender_evidence", "query": query, "top_k": top_k})
        normalized = query.casefold()
        hits: list[dict[str, Any]] = []
        for record in self.records.values():
            terms = record.search_terms
            matched = any(term.casefold() in normalized for term in terms) if terms else normalized in record.content.casefold()
            if matched:
                hits.append(
                    {
                        "evidence_ref": record.ref(context_read=False).model_dump(mode="json"),
                        "snippet": record.content[:240],
                    }
                )
        hits = hits[:top_k]
        return ToolResult(
            status=ToolResultStatus.OK if hits else ToolResultStatus.NO_RESULT,
            data={"case_id": self.case_id, "hits": hits},
            trace_id=self._trace_id("search"),
        )

    def read_context(
        self,
        *,
        evidence_id: str,
        before_blocks: int = 0,
        after_blocks: int = 0,
    ) -> ToolResult:
        self.calls.append(
            {
                "tool": "read_evidence_context",
                "evidence_id": evidence_id,
                "before_blocks": before_blocks,
                "after_blocks": after_blocks,
            }
        )
        record = self.records.get(evidence_id)
        if record is None:
            return ToolResult(
                status=ToolResultStatus.NO_RESULT,
                data={"evidence_id": evidence_id},
                trace_id=self._trace_id("read"),
                message="Evidence does not exist in the scoped case.",
            )
        self.read_evidence_ids.add(evidence_id)
        return ToolResult(
            status=ToolResultStatus.OK,
            data={
                "case_id": self.case_id,
                "evidence_ref": record.ref(context_read=True).model_dump(mode="json"),
                "content": record.content,
                "before": [],
                "after": [],
            },
            trace_id=self._trace_id("read"),
        )

    def compare_versions(self, *, document_key: str) -> ToolResult:
        self.calls.append({"tool": "compare_document_versions", "document_key": document_key})
        comparison = self.version_comparisons.get(
            document_key,
            {
                "document_key": document_key,
                "versions": [],
                "conflicts": [],
                "message": "No historical version was supplied to the prototype.",
            },
        )
        return ToolResult(
            status=ToolResultStatus.OK,
            data=comparison,
            trace_id=self._trace_id("compare"),
        )

    def validate_refs(
        self,
        *,
        refs: Sequence[EvidenceRef],
        manifest: DocumentManifest,
    ) -> ToolResult:
        self.calls.append(
            {
                "tool": "validate_evidence_refs",
                "evidence_ids": [item.evidence_id for item in refs],
                "manifest_version": manifest.manifest_version,
            }
        )
        active_documents = {
            (item.document_id, item.document_version)
            for item in manifest.active_documents
            if item.parse_status != "failed"
        }
        results: list[dict[str, Any]] = []
        for ref in refs:
            record = self.records.get(ref.evidence_id)
            reasons: list[str] = []
            if record is None:
                reasons.append("evidence_not_found")
            else:
                if record.block_id != ref.block_id or record.content_hash != ref.content_hash:
                    reasons.append("evidence_snapshot_mismatch")
                if (record.document_id, record.document_version) not in active_documents:
                    reasons.append("document_version_not_active")
            results.append(
                {
                    "evidence_id": ref.evidence_id,
                    "valid": not reasons,
                    "reasons": reasons,
                    "context_read_traced": ref.evidence_id in self.read_evidence_ids,
                }
            )
        return ToolResult(
            status=ToolResultStatus.OK,
            data={"case_id": self.case_id, "results": results},
            trace_id=self._trace_id("validate"),
        )


@dataclass
class ScriptedBidAnalysisModel:
    """Deterministic model double that emits real LangChain tool calls."""

    responses: list[AIMessage]
    _model_id: str = "scripted-demo-model"
    forced_response: AIMessage | None = None
    state_views: list[dict[str, Any]] = field(default_factory=list)

    @property
    def model_id(self) -> str:
        return self._model_id

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        *,
        system_prompt: str,
        state_view: dict[str, Any],
    ) -> AIMessage:
        del system_prompt
        self.state_views.append(dict(state_view))
        if (
            state_view.get("force_final_response")
            and self.forced_response is not None
        ):
            source = self.forced_response
            kwargs = dict(source.additional_kwargs)
            kwargs["bid_model_turn"] = True
            return AIMessage(
                content=source.content,
                tool_calls=list(source.tool_calls),
                additional_kwargs=kwargs,
            )
        turn = sum(
            1
            for message in messages
            if isinstance(message, AIMessage) and message.additional_kwargs.get("bid_model_turn")
        )
        if turn >= len(self.responses):
            return AIMessage(
                content=json.dumps(
                    build_manual_review_draft("scripted_model_exhausted").model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                additional_kwargs={"bid_model_turn": True},
            )
        source = self.responses[turn]
        kwargs = dict(source.additional_kwargs)
        kwargs["bid_model_turn"] = True
        return AIMessage(
            content=source.content,
            tool_calls=list(source.tool_calls),
            additional_kwargs=kwargs,
        )


def build_manual_review_draft(reason: str) -> AssessmentDraft:
    return AssessmentDraft(
        project_summary="Agent未能形成可验证的完整研判结果。",
        recommendation=Recommendation.MANUAL_REVIEW,
        dimension_reviews=[
            DimensionReview(
                dimension=dimension,
                status=DimensionStatus.UNRESOLVED,
                summary="未完成可靠研判。",
            )
            for dimension in REQUIRED_DIMENSIONS
        ],
        confidence=0,
        termination_reason=reason,
        unresolved_questions=["请人工检查资料和Agent运行轨迹。"],
    )


def build_demo_draft(records: dict[str, FakeEvidenceRecord]) -> AssessmentDraft:
    base_ref = records["EV-001"].ref(
        context_read=True,
        quote="投标截止时间为2026年8月20日，投标人须具备建筑装修装饰专业承包二级资质。",
    )
    commercial_ref = records["EV-002"].ref(
        context_read=True,
        quote="工期90日历天，进度款按月支付80%，投标保证金20万元。",
    )
    dimension_ref = {
        "schedule": commercial_ref,
        "payment": commercial_ref,
        "bond": commercial_ref,
    }
    reviews = [
        DimensionReview(
            dimension=dimension,
            status=DimensionStatus.CONFIRMED,
            summary=f"{dimension}已在招标资料中找到可验证依据。",
            evidence_refs=[dimension_ref.get(dimension, base_ref)],
        )
        for dimension in REQUIRED_DIMENSIONS
    ]
    return AssessmentDraft(
        project_summary="某办公楼装饰工程公开招标，资料基本完整，关键商务条件可识别。",
        recommendation=Recommendation.RECOMMEND_QUOTE,
        project_facts=[
            ProjectFact(
                field="bid_deadline",
                value="2026-08-20",
                normalized_value="2026-08-20",
                confidence=0.96,
                evidence_refs=[base_ref],
            ),
            ProjectFact(
                field="schedule_days",
                value="90日历天",
                normalized_value=90,
                unit="day",
                confidence=0.95,
                evidence_refs=[commercial_ref],
            ),
        ],
        dimension_reviews=reviews,
        key_findings=[
            Finding(
                claim_id="qualification-confirmed",
                dimension="qualification",
                title="资格门槛明确",
                conclusion="要求建筑装修装饰专业承包二级资质。",
                severity=Severity.HIGH,
                evidence_refs=[base_ref],
            )
        ],
        risks=[
            Finding(
                claim_id="payment-retention-risk",
                dimension="payment",
                title="进度款支付比例需要经营确认",
                conclusion="月度进度款支付80%，需要评估资金占用。",
                severity=Severity.HIGH,
                evidence_refs=[commercial_ref],
            )
        ],
        policy_factors=[
            PolicyFactorInput(
                factor_id="compliance_risk",
                rating=PolicyFactorRating.FAVORABLE,
                summary="当前资料未发现明确合规红线。",
                source_type=PolicyFactorSource.TENDER_EVIDENCE,
                confidence=0.85,
                evidence_refs=[base_ref],
            ),
            PolicyFactorInput(
                factor_id="qualification_fit",
                rating=PolicyFactorRating.FAVORABLE,
                summary="示例企业满足装饰专业承包二级资质要求。",
                source_type=PolicyFactorSource.TENDER_EVIDENCE,
                confidence=0.9,
                evidence_refs=[base_ref],
            ),
            PolicyFactorInput(
                factor_id="scope_cost_clarity",
                rating=PolicyFactorRating.ACCEPTABLE,
                summary="范围基本明确，仍需成本部复核清单。",
                source_type=PolicyFactorSource.TENDER_EVIDENCE,
                confidence=0.8,
                evidence_refs=[base_ref],
            ),
            PolicyFactorInput(
                factor_id="margin_potential",
                rating=PolicyFactorRating.ACCEPTABLE,
                summary="示例成本测算显示存在常规利润空间。",
                source_type=PolicyFactorSource.INTERNAL_DATA,
                source_note="示例成本测算快照",
                confidence=0.8,
            ),
            PolicyFactorInput(
                factor_id="payment_cashflow",
                rating=PolicyFactorRating.ADVERSE,
                summary="月度支付80%，存在一定资金占用。",
                source_type=PolicyFactorSource.TENDER_EVIDENCE,
                confidence=0.9,
                evidence_refs=[commercial_ref],
            ),
            PolicyFactorInput(
                factor_id="client_credit",
                rating=PolicyFactorRating.ACCEPTABLE,
                summary="示例客户信用处于可接受范围。",
                source_type=PolicyFactorSource.INTERNAL_DATA,
                source_note="示例客户台账",
                confidence=0.8,
            ),
            PolicyFactorInput(
                factor_id="delivery_capacity",
                rating=PolicyFactorRating.ACCEPTABLE,
                summary="90日历天工期可通过常规资源配置完成。",
                source_type=PolicyFactorSource.INTERNAL_DATA,
                source_note="示例项目资源计划",
                confidence=0.8,
            ),
            PolicyFactorInput(
                factor_id="strategic_value",
                rating=PolicyFactorRating.FAVORABLE,
                summary="项目可形成办公空间装饰标杆案例。",
                source_type=PolicyFactorSource.HUMAN_INPUT,
                source_note="示例总经办战略判断",
                confidence=0.75,
            ),
            PolicyFactorInput(
                factor_id="win_probability",
                rating=PolicyFactorRating.ACCEPTABLE,
                summary="竞争态势可接受。",
                source_type=PolicyFactorSource.HUMAN_INPUT,
                source_note="示例业务负责人判断",
                confidence=0.7,
            ),
            PolicyFactorInput(
                factor_id="bond_exposure",
                rating=PolicyFactorRating.ADVERSE,
                summary="20万元投标保证金形成短期资金占用。",
                source_type=PolicyFactorSource.TENDER_EVIDENCE,
                confidence=0.9,
                evidence_refs=[commercial_ref],
            ),
            PolicyFactorInput(
                factor_id="tender_readiness",
                rating=PolicyFactorRating.FAVORABLE,
                summary="投标截止时间和提交方式明确。",
                source_type=PolicyFactorSource.TENDER_EVIDENCE,
                confidence=0.9,
                evidence_refs=[base_ref],
            ),
        ],
        conflicts=[],
        missing_materials=[],
        unresolved_questions=[],
        confidence=0.9,
        termination_reason="analysis_complete",
    )


def build_demo_script(records: dict[str, FakeEvidenceRecord]) -> ScriptedBidAnalysisModel:
    draft = build_demo_draft(records)
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_tender_evidence",
                    "args": {"query": "项目 资格 截止 工期 付款 保证金", "top_k": 5},
                    "id": "call-search",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_evidence_context",
                    "args": {"evidence_id": "EV-001", "before_blocks": 0, "after_blocks": 1},
                    "id": "call-read-1",
                    "type": "tool_call",
                },
                {
                    "name": "read_evidence_context",
                    "args": {"evidence_id": "EV-002", "before_blocks": 0, "after_blocks": 1},
                    "id": "call-read-2",
                    "type": "tool_call",
                },
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "compare_document_versions",
                    "args": {"document_key": "tender_document"},
                    "id": "call-compare",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "get_bid_policy_rule",
                    "args": {"topic": "立项硬门槛"},
                    "id": "call-policy",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content=json.dumps(draft.model_dump(mode="json"), ensure_ascii=False),
        ),
    ]
    return ScriptedBidAnalysisModel(responses=responses)


def build_demo_evidence() -> dict[str, FakeEvidenceRecord]:
    return {
        "EV-001": FakeEvidenceRecord(
            evidence_id="EV-001",
            block_id="BLOCK-001",
            document_id="DOC-TENDER",
            document_version=2,
            content_hash="sha256-ev001-demo",
            content=(
                "项目名称：某办公楼装饰工程。投标截止时间为2026年8月20日10:00。"
                "投标人须具备建筑装修装饰专业承包二级资质。投标文件须在线提交。"
            ),
            locator=EvidenceLocator(page=3, section="招标公告"),
            search_terms=("项目", "资格", "截止", "投标"),
        ),
        "EV-002": FakeEvidenceRecord(
            evidence_id="EV-002",
            block_id="BLOCK-002",
            document_id="DOC-TENDER",
            document_version=2,
            content_hash="sha256-ev002-demo",
            content="计划工期90日历天。进度款按月审核后支付已完工程量的80%。投标保证金20万元。",
            locator=EvidenceLocator(page=18, section="合同主要条款"),
            search_terms=("工期", "付款", "保证金"),
        ),
    }


def build_demo_manifest() -> DocumentManifest:
    return DocumentManifest.model_validate(
        {
            "case_id": "CASE-DEMO-001",
            "manifest_version": 1,
            "manifest_hash": "manifest-demo-001",
            "documents": [
                {
                    "document_id": "DOC-TENDER",
                    "file_name": "某办公楼装饰工程招标文件.pdf",
                    "document_type": "tender_document",
                    "document_version": 2,
                    "sha256": "sha256-document-demo",
                    "parse_status": "ready",
                    "active": True,
                }
            ],
        }
    )
