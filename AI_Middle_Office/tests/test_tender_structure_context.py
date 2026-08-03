from __future__ import annotations

import hashlib
import time

from mcp_servers.tender_evidence.auth import TenderScope
from mcp_servers.tender_evidence.contracts import (
    EvidenceBlock,
    EvidenceLocator,
    EvidenceStructuralContext,
)
from mcp_servers.tender_evidence.service import TenderEvidenceService
from mcp_servers.tender_evidence.structure_context import (
    build_structural_context_map,
)


CASE_ID = "33333333-3333-4333-8333-333333333333"


def _block(
    *,
    evidence_id: str,
    order: int,
    content: str,
    source_location: str,
    document_id: str = "doc-1",
) -> EvidenceBlock:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return EvidenceBlock(
        evidence_id=evidence_id,
        block_id=f"BLK-{evidence_id}",
        document_id=document_id,
        document_key="design-task",
        document_version=1,
        block_order=order,
        locator=EvidenceLocator(
            section="技术要求",
            source_location=source_location,
        ),
        content_hash=digest,
        content=content,
    )


def test_structural_context_resolves_existing_heading_and_table_header():
    heading = _block(
        evidence_id="EV-heading",
        order=0,
        content="八、设计成果要求",
        source_location="DOCX第20段",
    )
    header = _block(
        evidence_id="EV-header",
        order=1,
        content="设计阶段 | 主要工作内容 | 成果要求",
        source_location="DOCX表2第1行",
    )
    child = _block(
        evidence_id="EV-child",
        order=2,
        content="方案设计 | 空间总体规划 | 平面规划图",
        source_location="DOCX表2第2行",
    )
    contexts = build_structural_context_map(
        candidate_evidence_ids=[child.evidence_id],
        document_blocks=[heading, header, child],
    )

    resolved = contexts[child.evidence_id]
    assert [item.relation for item in resolved] == [
        "section_parent",
        "table_header_parent",
    ]
    assert [
        item.evidence_ref.evidence_id for item in resolved
    ] == ["EV-heading", "EV-header"]


def test_structural_context_never_crosses_document_versions():
    heading = _block(
        evidence_id="EV-heading-other",
        order=0,
        content="八、设计成果要求",
        source_location="DOCX第20段",
        document_id="doc-other",
    )
    child = _block(
        evidence_id="EV-child",
        order=2,
        content="方案设计 | 空间总体规划 | 平面规划图",
        source_location="DOCX表2第2行",
    )
    contexts = build_structural_context_map(
        candidate_evidence_ids=[child.evidence_id],
        document_blocks=[heading, child],
    )

    assert contexts.get(child.evidence_id, []) == []


class _StructuredRepository:
    def __init__(
        self,
        *,
        child: EvidenceBlock,
        parent: EvidenceBlock,
    ):
        self.child = child
        self.parent = parent
        self.structural_lookup_count = 0

    def search(self, **_kwargs):
        return [self.child]

    def get_structural_context(self, **_kwargs):
        self.structural_lookup_count += 1
        return {
            self.child.evidence_id: [
                EvidenceStructuralContext(
                    relation="table_header_parent",
                    content=self.parent.content,
                    evidence_ref=self.parent.to_ref(
                        context_read=False,
                        quote=self.parent.content,
                    ),
                )
            ]
        }


def test_service_exposes_structural_parent_without_consuming_top_k():
    parent = _block(
        evidence_id="EV-header",
        order=0,
        content="设计阶段 | 主要工作内容 | 成果要求",
        source_location="DOCX表2第1行",
    )
    child = _block(
        evidence_id="EV-child",
        order=1,
        content="方案设计 | 空间总体规划 | 平面规划图",
        source_location="DOCX表2第2行",
    )
    repository = _StructuredRepository(child=child, parent=parent)
    now = int(time.time())
    scope = TenderScope(
        case_id=CASE_ID,
        assessment_id="assessment-1",
        agent_run_id="run-1",
        subject="tester",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="test",
        issuer="test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=5,
        enable_candidate_coverage_selection=True,
        enable_structured_context_groups=True,
    )

    envelope = service.search_tender_evidence(
        query="方案设计需要完成哪些工作与成果？",
        top_k=1,
    )

    assert repository.structural_lookup_count == 1
    assert len(envelope.data["matches"]) == 1
    match = envelope.data["matches"][0]
    assert match["evidence_ref"]["evidence_id"] == "EV-child"
    assert (
        match["structural_context_group"]["members"][0][
            "evidence_ref"
        ]["evidence_id"]
        == "EV-header"
    )
    summary = envelope.data["query_plan"][
        "structured_context_summary"
    ]
    assert summary["lookup_count"] == 1
    assert summary["contextualized_candidate_count"] == 1
    assert summary["table_header_parent_count"] == 1


class _StructuredSiblingRepository:
    def __init__(
        self,
        *,
        children: list[EvidenceBlock],
        parent: EvidenceBlock,
    ):
        self.children = children
        self.parent = parent

    def search(self, **_kwargs):
        return self.children

    def get_structural_context(self, **_kwargs):
        return {
            child.evidence_id: [
                EvidenceStructuralContext(
                    relation="table_header_parent",
                    content=self.parent.content,
                    evidence_ref=self.parent.to_ref(
                        context_read=False,
                        quote=self.parent.content,
                    ),
                )
            ]
            for child in self.children
        }


def test_structured_siblings_expand_one_parent_without_extra_search():
    parent = _block(
        evidence_id="EV-header",
        order=0,
        content="设计阶段 | 主要工作内容 | 成果要求",
        source_location="DOCX表2第1行",
    )
    children = [
        _block(
            evidence_id=f"EV-child-{index}",
            order=index,
            content=content,
            source_location=f"DOCX表2第{index + 1}行",
        )
        for index, content in enumerate(
            (
                "方案设计 | 空间总体规划 | 平面规划图",
                "深化设计 | 材料选型 | 材料选型表",
                "施工图设计 | 全套施工图 | 节点大样图",
            ),
            start=1,
        )
    ]
    repository = _StructuredSiblingRepository(
        children=children,
        parent=parent,
    )
    now = int(time.time())
    scope = TenderScope(
        case_id=CASE_ID,
        assessment_id="assessment-2",
        agent_run_id="run-2",
        subject="tester",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="test",
        issuer="test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=5,
        enable_candidate_coverage_selection=True,
        enable_structured_context_groups=True,
    )

    envelope = service.search_tender_evidence(
        query=(
            "方案设计、深化设计和施工图设计分别需要完成"
            "哪些工作与成果？"
        ),
        top_k=1,
    )

    assert len(envelope.data["matches"]) == 1
    group = envelope.data["matches"][0][
        "context_evidence_group"
    ]
    assert 1 <= group["member_count"] <= 3
    assert all(
        item["evidence_ref"]["evidence_id"]
        != envelope.data["matches"][0]["evidence_ref"]["evidence_id"]
        for item in group["members"]
    )
    summary = envelope.data["query_plan"][
        "structured_sibling_group_summary"
    ]
    assert summary["selected_parent_count"] == 1
    assert summary["additional_retrieval_query_count"] == 0
