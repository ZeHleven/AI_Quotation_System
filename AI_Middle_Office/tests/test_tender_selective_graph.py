from __future__ import annotations

import hashlib
import time

import pytest

from app.agents.bid_intake.retrieval_evaluation import (
    sanitize_query_plan_for_evaluation,
)
from mcp_servers.tender_evidence.auth import TenderScope
from mcp_servers.tender_evidence.contracts import (
    DocumentItem,
    DocumentManifest,
    EvidenceBlock,
    EvidenceLocator,
)
from mcp_servers.tender_evidence.selective_graph import (
    decide_graph_trigger,
    exact_reference_target_matches,
    extract_verified_exact_references,
    is_verified_table_parent_seed,
    verified_structural_children,
)
from mcp_servers.tender_evidence.service import TenderEvidenceService


def _block(
    evidence_id: str,
    *,
    document_id: str,
    document_key: str,
    block_order: int,
    source_location: str,
    content: str,
) -> EvidenceBlock:
    return EvidenceBlock(
        evidence_id=evidence_id,
        block_id=f"BLOCK-{evidence_id}",
        document_id=document_id,
        document_key=document_key,
        document_version=1,
        block_order=block_order,
        locator=EvidenceLocator(
            section="测试章节",
            source_location=source_location,
        ),
        content_hash=hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest(),
        content=content,
    )


def _manifest() -> DocumentManifest:
    return DocumentManifest(
        case_id="CASE-GRAPH-001",
        manifest_version=1,
        manifest_hash="a" * 64,
        documents=[
            DocumentItem(
                document_id="DOC-TENDER",
                document_key="tender",
                file_name="招标文件.docx",
                document_type="tender_document",
                document_version=1,
                sha256="b" * 64,
                parse_status="ready",
            ),
            DocumentItem(
                document_id="DOC-BOQ",
                document_key="boq",
                file_name="工程量清单.xlsx",
                document_type="bill_of_quantities",
                document_version=1,
                sha256="c" * 64,
                parse_status="ready",
            ),
            DocumentItem(
                document_id="DOC-CLARIFICATION",
                document_key="clarification",
                file_name="投标答疑.xlsx",
                document_type="clarification",
                document_version=1,
                sha256="d" * 64,
                parse_status="ready",
            ),
        ],
    )


class _GraphRepository:
    def __init__(
        self,
        *,
        search_blocks: list[EvidenceBlock],
        contexts: dict[str, list[EvidenceBlock]] | None = None,
        exact_blocks: list[EvidenceBlock] | None = None,
    ):
        self.search_blocks = search_blocks
        self.contexts = contexts or {}
        self.exact_blocks = exact_blocks or []
        self.context_calls = 0
        self.exact_lookup_calls = 0
        self.manifest_calls = 0

    def get_manifest(self, *, case_id: str) -> DocumentManifest:
        assert case_id == "CASE-GRAPH-001"
        self.manifest_calls += 1
        return _manifest()

    def search(
        self,
        *,
        case_id: str,
        query: str,
        top_k: int,
        search_mode: str = "hybrid",
    ) -> list[EvidenceBlock]:
        assert case_id == "CASE-GRAPH-001"
        del search_mode
        if query == "23 TK1":
            self.exact_lookup_calls += 1
            return self.exact_blocks[:top_k]
        return self.search_blocks[:top_k]

    def get_context(
        self,
        *,
        case_id: str,
        evidence_id: str,
        before_blocks: int,
        after_blocks: int,
    ) -> list[EvidenceBlock]:
        assert case_id == "CASE-GRAPH-001"
        assert before_blocks == 0
        assert after_blocks == 4
        self.context_calls += 1
        return self.contexts.get(evidence_id, [])


def _scope() -> TenderScope:
    now = int(time.time())
    return TenderScope(
        case_id="CASE-GRAPH-001",
        assessment_id="ASSESSMENT-GRAPH-001",
        agent_run_id="RUN-GRAPH-001",
        subject="graph-test",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="test",
        issuer="test",
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "招标文件与施工合同的初验付款比例是否一致？",
            True,
        ),
        (
            "投标答疑是否已经同步到工程量清单？",
            True,
        ),
        (
            "仅凭当前四份资料还缺什么？",
            False,
        ),
        (
            "工程量清单包含哪些项目？",
            False,
        ),
    ],
)
def test_graph_trigger_requires_roles_and_relation_intent(
    question: str,
    expected: bool,
) -> None:
    assert decide_graph_trigger(question).triggered is expected


def test_verified_structure_expands_only_same_table_children() -> None:
    header = _block(
        "EV-HEADER",
        document_id="DOC-BOQ",
        document_key="boq",
        block_order=10,
        source_location="费用汇总表 第2行",
        content="序号 | 户型 | 面积 | 数量",
    )
    children = [
        _block(
            f"EV-ROW-{index}",
            document_id="DOC-BOQ",
            document_key="boq",
            block_order=10 + index,
            source_location=f"费用汇总表 第{2 + index}行",
            content=f"{index} | 标准房型{index} | 49.{index} | 1",
        )
        for index in range(1, 5)
    ]
    other_sheet = _block(
        "EV-OTHER",
        document_id="DOC-BOQ",
        document_key="boq",
        block_order=15,
        source_location="其他表 第7行",
        content="1 | 无关项目 | 10 | 1",
    )

    assert is_verified_table_parent_seed(header) is True
    expanded = verified_structural_children(
        seed=header,
        context_blocks=[header, *children, other_sheet],
    )

    assert [item.evidence_id for item in expanded] == [
        "EV-ROW-1",
        "EV-ROW-2",
        "EV-ROW-3",
        "EV-ROW-4",
    ]


@pytest.mark.parametrize(
    "content",
    [
        "4 | 招标范围 | 酒店项目精装修施工面积约260平方米",
        "5 | TK1清单第23项 | 项目名称调整为结晶处理",
        "一 | 客房 | 标准大床房 | 数量2间",
    ],
)
def test_table_parent_rejects_business_rows_with_header_words(
    content: str,
) -> None:
    row = _block(
        "EV-DATA-ROW",
        document_id="DOC-BOQ",
        document_key="boq",
        block_order=10,
        source_location="费用汇总表 第2行",
        content=content,
    )

    assert is_verified_table_parent_seed(row) is False


def test_exact_list_reference_requires_identifier_and_qualifier() -> None:
    references = extract_verified_exact_references(
        "TK1户型清单序号23美缝修改为结晶处理"
    )
    assert len(references) == 1
    assert references[0].identifier == "23"
    assert references[0].qualifiers == ("TK1",)
    assert references[0].lookup_query == "23 TK1"
    assert references[0].resolvable is True
    matching = _block(
        "EV-BOQ-23",
        document_id="DOC-BOQ",
        document_key="boq",
        block_order=23,
        source_location="清单 第24行",
        content="23 | 客房 | 标准大床房（TK1） | 地面瓷砖美缝处理",
    )
    wrong_scope = matching.model_copy(
        update={
            "evidence_id": "EV-BOQ-23-WRONG",
            "content": "23 | 公区 | 电梯厅装饰",
        }
    )

    assert exact_reference_target_matches(
        reference=references[0],
        target_document_role="bill_of_quantities",
        block=matching,
    )
    assert not exact_reference_target_matches(
        reference=references[0],
        target_document_role="bill_of_quantities",
        block=wrong_scope,
    )


def test_service_attaches_bounded_structural_graph_group() -> None:
    tender = _block(
        "EV-TENDER",
        document_id="DOC-TENDER",
        document_key="tender",
        block_order=1,
        source_location="DOCX第1段",
        content="招标范围装修面积约260平方米",
    )
    header = _block(
        "EV-HEADER",
        document_id="DOC-BOQ",
        document_key="boq",
        block_order=10,
        source_location="费用汇总表 第2行",
        content="序号 | 户型 | 面积 | 数量",
    )
    children = [
        _block(
            f"EV-CHILD-{index}",
            document_id="DOC-BOQ",
            document_key="boq",
            block_order=10 + index,
            source_location=f"费用汇总表 第{2 + index}行",
            content=f"{index} | 房型{index} | {40 + index} | 1",
        )
        for index in range(1, 5)
    ]
    distractors = [
        _block(
            f"EV-DISTRACTOR-{index}",
            document_id="DOC-TENDER",
            document_key="tender",
            block_order=20 + index,
            source_location=f"DOCX第{20 + index}段",
            content=f"面积相关说明{index}",
        )
        for index in range(1, 4)
    ]
    repository = _GraphRepository(
        search_blocks=[tender, header, *distractors],
        contexts={"EV-HEADER": [header, *children]},
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=_scope,
        per_query_candidate_top_k=5,
        enable_selective_graph_expansion=True,
    )

    result = service.search_tender_evidence(
        query="招标文件与工程量清单的面积是否一致，差异多大？",
        top_k=5,
    )

    matches = result.data["matches"]
    header_match = next(
        item
        for item in matches
        if item["evidence_ref"]["evidence_id"] == "EV-HEADER"
    )
    members = header_match["context_evidence_group"]["members"]
    assert [
        item["evidence_ref"]["evidence_id"] for item in members
    ] == [
        "EV-CHILD-1",
        "EV-CHILD-2",
        "EV-CHILD-3",
        "EV-CHILD-4",
    ]
    summary = result.data["query_plan"][
        "selective_graph_expansion_summary"
    ]
    assert summary["triggered"] is True
    assert summary["graph_call_count"] == 1
    assert summary["seed_count"] == 1
    assert summary["expanded_evidence_count"] == 4
    assert summary["edge_type_counts"] == {
        "child_of_section_or_table": 4
    }
    assert repository.context_calls == 1


def test_service_resolves_exact_reference_without_reordering_anchors() -> None:
    clarification = _block(
        "EV-CLARIFICATION",
        document_id="DOC-CLARIFICATION",
        document_key="clarification",
        block_order=5,
        source_location="Sheet1 第6行",
        content="TK1户型清单序号23美缝修改为结晶处理",
    )
    target = _block(
        "EV-BOQ-23",
        document_id="DOC-BOQ",
        document_key="boq",
        block_order=66,
        source_location="清单 第28行",
        content="23 | 客房 | 标准大床房（TK1） | 地面瓷砖美缝处理",
    )
    distractors = [
        _block(
            f"EV-DISTRACTOR-{index}",
            document_id="DOC-CLARIFICATION",
            document_key="clarification",
            block_order=10 + index,
            source_location=f"Sheet1 第{10 + index}行",
            content=f"答疑变更说明{index}",
        )
        for index in range(1, 5)
    ]
    repository = _GraphRepository(
        search_blocks=[clarification, *distractors],
        exact_blocks=[clarification, target],
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=_scope,
        per_query_candidate_top_k=5,
        enable_selective_graph_expansion=True,
    )

    result = service.search_tender_evidence(
        query="投标答疑是否已经同步到工程量清单，是否存在差异？",
        top_k=5,
    )

    assert [
        item["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
    ] == [
        "EV-CLARIFICATION",
        "EV-DISTRACTOR-1",
        "EV-DISTRACTOR-2",
        "EV-DISTRACTOR-3",
        "EV-DISTRACTOR-4",
    ]
    members = result.data["matches"][0][
        "context_evidence_group"
    ]["members"]
    assert members[0]["evidence_ref"]["evidence_id"] == "EV-BOQ-23"
    assert members[0]["graph_path"]["edge_type"] == (
        "exactly_references"
    )
    summary = result.data["query_plan"][
        "selective_graph_expansion_summary"
    ]
    assert summary["reference_lookup_count"] == 1
    assert summary["resolved_reference_count"] == 1
    assert summary["expanded_evidence_count"] == 1
    assert repository.exact_lookup_calls == 1


def test_service_skips_ambiguous_reference_without_qualifier() -> None:
    clarification = _block(
        "EV-CLARIFICATION",
        document_id="DOC-CLARIFICATION",
        document_key="clarification",
        block_order=5,
        source_location="Sheet1 第6行",
        content="清单序号23修改为结晶处理",
    )
    repository = _GraphRepository(search_blocks=[clarification])
    service = TenderEvidenceService(
        repository,
        scope_provider=_scope,
        enable_selective_graph_expansion=True,
    )

    result = service.search_tender_evidence(
        query="投标答疑是否已经同步到工程量清单，是否存在差异？",
        top_k=5,
    )

    summary = result.data["query_plan"][
        "selective_graph_expansion_summary"
    ]
    assert summary["unresolvable_reference_count"] == 1
    assert summary["reference_lookup_count"] == 0
    assert summary["expanded_evidence_count"] == 0
    assert repository.exact_lookup_calls == 0


def test_service_does_not_call_graph_for_single_document_question() -> None:
    block = _block(
        "EV-BOQ",
        document_id="DOC-BOQ",
        document_key="boq",
        block_order=1,
        source_location="清单 第1行",
        content="工程量清单编制说明",
    )
    repository = _GraphRepository(search_blocks=[block])
    service = TenderEvidenceService(
        repository,
        scope_provider=_scope,
        enable_selective_graph_expansion=True,
    )

    result = service.search_tender_evidence(
        query="工程量清单的编制说明是什么？",
        top_k=5,
    )

    summary = result.data["query_plan"][
        "selective_graph_expansion_summary"
    ]
    assert summary["triggered"] is False
    assert summary["graph_call_count"] == 0
    assert summary["expanded_evidence_count"] == 0
    assert repository.manifest_calls == 0


def test_graph_summary_sanitizer_removes_locator_and_reference_text() -> None:
    sanitized = sanitize_query_plan_for_evaluation(
        {
            "selective_graph_expansion_summary": {
                "enabled": True,
                "triggered": True,
                "document_roles": [
                    "tender_document",
                    "bill_of_quantities",
                ],
                "relation_signals": ["consistency_or_conflict"],
                "graph_call_count": 1,
                "expanded_evidence_count": 1,
                "paths": [
                    {
                        "hop_count": 1,
                        "seed_evidence_id": "EV-SEED",
                        "edge_type": "exactly_references",
                        "target_evidence_id": "EV-TARGET",
                        "target_document_role": (
                            "bill_of_quantities"
                        ),
                        "reference_type": (
                            "bill_of_quantities_item"
                        ),
                        "target_locator": {
                            "source_location": "秘密项目清单第28行"
                        },
                        "reference_text": "秘密项目清单序号23",
                    }
                ],
            }
        }
    )

    serialized = str(sanitized)
    assert "秘密项目" not in serialized
    path = sanitized["selective_graph_expansion_summary"][
        "paths"
    ][0]
    assert "target_locator" not in path
    assert path["edge_type"] == "exactly_references"
