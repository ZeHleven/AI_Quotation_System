from __future__ import annotations

import time

from mcp_servers.tender_evidence.auth import TenderScope
from mcp_servers.tender_evidence.contracts import (
    EvidenceBlock,
    EvidenceLocator,
    EvidenceStructuralContext,
    ResultStatus,
)
from mcp_servers.tender_evidence.query_planner import (
    match_block_sufficiency_needs,
    merge_planned_results,
    plan_tender_query,
)
from mcp_servers.tender_evidence.retrieval_router import (
    route_tender_query,
)
from mcp_servers.tender_evidence.service import TenderEvidenceService


def _block(
    evidence_id: str,
    content: str,
    order: int,
    *,
    section: str | None = None,
    document_id: str = "DOC-QUERY-PLAN",
) -> EvidenceBlock:
    return EvidenceBlock(
        evidence_id=evidence_id,
        block_id=f"BLK-{evidence_id}",
        document_id=document_id,
        document_key="tender-document",
        document_version=1,
        block_order=order,
        locator=EvidenceLocator(page=order + 1, section=section),
        content_hash=(str(order + 1) * 64)[:64],
        content=content,
        keywords=[],
    )


def test_query_planner_keeps_one_topic_query_unchanged() -> None:
    plan = plan_tender_query("投标保证金是多少？")
    assert plan.strategy == "single_query"
    assert plan.queries == ("投标保证金是多少？",)
    assert plan.atomic_queries == ()


def test_query_planner_adds_auditable_fact_companion_for_risk() -> None:
    original = "第十一条付款安排对现金流有哪些不利影响？"

    baseline = plan_tender_query(original)
    candidate = plan_tender_query(
        original,
        enable_semantic_fact_companion=True,
    )

    assert baseline.supporting_queries == ()
    assert candidate.queries == (original,)
    assert candidate.supporting_queries == (
        "项目付款、结算、审计和回款条件",
    )
    assert candidate.supporting_topics == ("payment",)
    assert candidate.supporting_strategy == "semantic_fact_companion"
    assert candidate.to_payload()["query_count"] == 1
    assert candidate.to_payload()["supporting_query_count"] == 1


def test_query_planner_extracts_generic_atomic_fact_slots() -> None:
    cases = {
        "投标担保金额和缴纳要求是什么？": (
            ("投标担保金额", "缴纳要求"),
            ("amount", "requirement"),
        ),
        "商业街区和32层办公区的承包范围分别包含什么，哪些工程不包含？": (
            (
                "商业街区",
                "32层办公区的承包范围 包含",
                "工程 不包含",
            ),
            ("entity_fact", "inclusion", "exclusion"),
        ),
        "投标文件要递交到哪里，需要准备几套纸质文件和几份电子文件？": (
            (
                "投标文件递交地点",
                "纸质文件 套数",
                "电子文件 份数",
            ),
            ("location", "quantity", "quantity"),
        ),
    }

    for original, expected in cases.items():
        baseline = plan_tender_query(original)
        candidate = plan_tender_query(
            original,
            enable_atomic_fact_slots=True,
        )

        assert baseline.fact_slot_queries == ()
        assert candidate.fact_slot_queries == expected[0]
        assert candidate.fact_slot_types == expected[1]
        assert candidate.fact_slot_strategy == (
            "compound_surface_fact_slots"
        )
        assert candidate.to_payload()["query_count"] == len(
            candidate.queries
        )


def test_query_planner_does_not_expand_a_single_fact_question() -> None:
    plan = plan_tender_query(
        "投标保证金是多少？",
        enable_atomic_fact_slots=True,
    )

    assert plan.fact_slot_queries == ()
    assert plan.fact_slot_types == ()
    assert plan.fact_slot_strategy is None


def test_sufficiency_assessment_builds_relations_without_selection() -> None:
    query = "方案甲、方案乙的时间节点分别是什么？"

    plan = plan_tender_query(
        query,
        enable_evidence_sufficiency_assessment=True,
    )

    assert plan.queries == (query,)
    assert plan.coverage_need_queries == ()
    assert plan.coverage_selection_policy == "off"
    assert plan.sufficiency_need_subjects == ("方案甲", "方案乙")
    assert plan.sufficiency_need_types == ("time", "time")
    assert plan.sufficiency_need_answer_shapes == (
        "time_value",
        "time_value",
    )
    assert plan.sufficiency_relation_shape_supported is True
    assert plan.to_payload()["sufficiency_need_count"] == 2


def test_sufficiency_assessment_fails_closed_for_unsupported_shape() -> None:
    plan = plan_tender_query(
        "第十一条付款安排对现金流有哪些不利影响？",
        enable_evidence_sufficiency_assessment=True,
    )

    assert plan.sufficiency_need_queries == ()
    assert plan.sufficiency_relation_shape_supported is False
    assert (
        plan.sufficiency_relation_shape_reason
        == "unsupported_relation_shape"
    )
    assert plan.coverage_selection_policy == "off"


def test_sufficiency_assessment_rejects_three_weak_negative_shapes() -> None:
    examples = (
        (
            "资料是否明确设计费付款比例、付款节点和结算条件？",
            "本章介绍设计费用管理原则及设计成果评分办法。",
            3,
        ),
        (
            "资料是否明确投标保证金金额、缴纳方式和退还条件？",
            "本项目履约保证金相关事项将在合同谈判阶段讨论。",
            3,
        ),
        (
            "资料是否提供设计单位资质等级、项目负责人资格和设计团队最低人数要求？",
            "主创设计师资历与深化团队配置参与综合评分。",
            3,
        ),
    )

    for index, (query, weak_content, expected_need_count) in enumerate(
        examples,
        start=1,
    ):
        plan = plan_tender_query(
            query,
            enable_evidence_sufficiency_assessment=True,
        )
        matches = match_block_sufficiency_needs(
            plan=plan,
            block=_block(
                f"EV-WEAK-SUFFICIENCY-{index}",
                weak_content,
                index,
            ),
        )

        assert len(plan.sufficiency_need_queries) == expected_need_count
        assert plan.sufficiency_relation_shape_supported is True
        assert matches == ()


def test_sufficiency_assessment_is_invariant_to_subject_renaming() -> None:
    examples = (
        (
            "区域甲、区域乙的时间节点分别是什么？",
            (
                "区域甲在10个工作日内完成。",
                "区域乙在6个工作日内完成。",
            ),
            "区域甲和区域乙的相关安排见后续说明。",
        ),
        (
            "设备东、设备西的时间节点分别是什么？",
            (
                "设备东在10个工作日内完成。",
                "设备西在6个工作日内完成。",
            ),
            "设备东和设备西的相关安排见后续说明。",
        ),
    )
    signatures = []

    for example_index, (query, direct_contents, weak_content) in enumerate(
        examples,
        start=1,
    ):
        plan = plan_tender_query(
            query,
            enable_evidence_sufficiency_assessment=True,
        )
        direct_matches = tuple(
            tuple(
                match.need_index
                for match in match_block_sufficiency_needs(
                    plan=plan,
                    block=_block(
                        (
                            "EV-RENAMED-DIRECT-"
                            f"{example_index}-{content_index}"
                        ),
                        content,
                        content_index,
                    ),
                )
            )
            for content_index, content in enumerate(
                direct_contents,
                start=1,
            )
        )
        weak_matches = match_block_sufficiency_needs(
            plan=plan,
            block=_block(
                f"EV-RENAMED-WEAK-{example_index}",
                weak_content,
                9,
            ),
        )
        signatures.append(
            (
                plan.sufficiency_need_types,
                plan.sufficiency_need_answer_shapes,
                direct_matches,
                tuple(
                    match.need_index for match in weak_matches
                ),
            )
        )

    assert signatures[0] == signatures[1]
    assert signatures[0] == (
        ("time", "time"),
        ("time_value", "time_value"),
        ((0,), (1,)),
        (),
    )


def test_query_planner_builds_coverage_needs_without_search_queries() -> None:
    original = (
        "投标文件要递交到哪里，需要准备几套纸质文件和几份电子文件？"
    )
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )

    assert plan.fact_slot_queries == ()
    assert plan.coverage_need_queries == (
        "投标文件递交地点",
        "纸质文件 套数",
        "电子文件 份数",
    )
    assert plan.coverage_need_types == (
        "location",
        "quantity",
        "quantity",
    )
    assert plan.coverage_strategy == "answer_signal_need_coverage"
    assert plan.to_payload()["query_count"] == len(plan.queries)
    assert plan.to_payload()["coverage_need_count"] == 3


def test_query_planner_decomposes_compound_tender_dimensions() -> None:
    plan = plan_tender_query(
        "项目的付款条件、工期风险和投标保证金分别是什么？"
    )
    assert plan.strategy == "topic_decomposition"
    assert plan.queries[0] == (
        "项目的付款条件、工期风险和投标保证金分别是什么？"
    )
    assert plan.topics == ("payment", "schedule", "bond")
    assert len(plan.queries) == 4
    assert any("付款" in item for item in plan.atomic_queries)
    assert any("工期" in item for item in plan.atomic_queries)
    assert any("保证金" in item for item in plan.atomic_queries)


def test_query_planner_splits_independent_clauses_and_caps_work() -> None:
    plan = plan_tender_query(
        "现场交通组织如何？是否允许夜间施工？临时用电由谁提供？",
        max_query_count=3,
    )
    assert plan.strategy == "clause_decomposition"
    assert len(plan.queries) == 3
    assert plan.queries[1] == "现场交通组织如何"
    assert plan.queries[2] == "是否允许夜间施工"


def test_multi_query_merge_preserves_atomic_query_coverage() -> None:
    plan = plan_tender_query(
        "项目的付款条件、工期风险和投标保证金分别是什么？"
    )
    shared = _block("EV-SHARED", "综合说明", 0)
    payment = _block("EV-PAYMENT", "付款条件", 1)
    schedule = _block("EV-SCHEDULE", "工期风险", 2)
    bond = _block("EV-BOND", "投标保证金", 3)
    merged = merge_planned_results(
        plan=plan,
        ranked_results=[
            (plan.queries[0], [shared]),
            (plan.queries[1], [payment]),
            (plan.queries[2], [schedule]),
            (plan.queries[3], [bond]),
        ],
        top_k=3,
    )
    assert [item.block.evidence_id for item in merged] == [
        "EV-PAYMENT",
        "EV-SCHEDULE",
        "EV-BOND",
    ]


def test_anchor_preserving_no_promotion_matches_multi_query_baseline() -> None:
    original = "请分别说明付款条件、工期风险和质量验收标准。"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "anchor_preserving_direct_alignment"
        ),
    )
    assert candidate_plan.queries == baseline_plan.queries

    shared = [
        _block(
            f"EV-SHARED-{index}",
            f"综合资料索引{index}",
            index,
        )
        for index in range(1, 6)
    ]
    diversity_anchors = [
        _block("EV-AUX-PAYMENT", "辅助检索结果甲", 10),
        _block("EV-AUX-SCHEDULE", "辅助检索结果乙", 11),
        _block("EV-AUX-QUALITY", "辅助检索结果丙", 12),
    ]
    ranked_results = [
        (baseline_plan.queries[0], shared),
        (
            baseline_plan.queries[1],
            [diversity_anchors[0], *shared],
        ),
        (
            baseline_plan.queries[2],
            [diversity_anchors[1], *shared],
        ),
        (
            baseline_plan.queries[3],
            [diversity_anchors[2], *shared],
        ),
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    baseline_ids = [item.block.evidence_id for item in baseline]
    candidate_ids = [item.block.evidence_id for item in candidate]

    assert baseline_ids == [
        "EV-AUX-PAYMENT",
        "EV-AUX-SCHEDULE",
        "EV-AUX-QUALITY",
        "EV-SHARED-1",
        "EV-SHARED-2",
    ]
    assert candidate_ids == baseline_ids
    assert sum(item.promoted_by_coverage for item in candidate) == 0


def test_candidate_coverage_prefers_answer_over_keyword_decoys() -> None:
    original = "投标担保金额和缴纳要求是什么？"
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )
    decoy = _block(
        "EV-DECOY",
        "投标担保金额超过损失时，投标人还应承担赔偿责任。",
        0,
    )
    blank_form = _block(
        "EV-BLANK",
        "投标担保采用现金或保函，金额大写元、小写元。",
        1,
    )
    answer = _block(
        "EV-ANSWER",
        "12 投标担保 金额为RMB200,000.00元，或同等额度银行保函。",
        2,
    )

    merged = merge_planned_results(
        plan=plan,
        ranked_results=[
            (
                original,
                [
                    decoy,
                    blank_form,
                    _block("EV-OTHER-1", "其他说明", 3),
                    _block("EV-OTHER-2", "其他说明", 4),
                    _block("EV-OTHER-3", "其他说明", 5),
                    answer,
                ],
            )
        ],
        top_k=3,
    )

    assert merged[0].block.evidence_id == "EV-ANSWER"
    assert merged[0].selected_by_coverage is True
    assert merged[0].coverage_need_indexes == (0,)
    assert merged[0].coverage_need_types == ("amount",)


def test_anchor_preserving_coverage_keeps_direct_relation_anchor() -> None:
    original = (
        "招标文件和施工合同是否已经完整约定履约保证金的金额、"
        "提交方式、提交节点与退还条件？"
    )
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "anchor_preserving_direct_alignment"
        ),
    )
    tender_bond = _block(
        "EV-TENDER-BOND",
        "招标文件：投标保证金的提交形式为无。",
        0,
    )
    generic_submit = _block(
        "EV-GENERIC-SUBMIT",
        "乙方应向甲方提交完整、真实、有效的工程结算文件。",
        1,
    )
    generic_payment = _block(
        "EV-GENERIC-PAYMENT",
        "每月25日提交本月进度款申请。",
        2,
    )
    performance_bond = _block(
        "EV-PERFORMANCE-BOND",
        "验收合格后付至合同总价的90%，同时无息返还履约保证金。",
        3,
    )
    other = _block("EV-OTHER", "其他合同说明。", 4)
    late_generic_submit = _block(
        "EV-LATE-SUBMIT",
        "投标人应按招标文件规定提交投标资料。",
        5,
    )
    late_settlement = _block(
        "EV-LATE-SETTLEMENT",
        "乙方逾期提交结算文件超过30日的，甲方可单方结算。",
        6,
    )

    merged = merge_planned_results(
        plan=plan,
        ranked_results=[
            (
                original,
                [
                    tender_bond,
                    generic_submit,
                    generic_payment,
                    performance_bond,
                    other,
                    late_generic_submit,
                    late_settlement,
                ],
            )
        ],
        top_k=5,
    )

    assert [item.block.evidence_id for item in merged] == [
        "EV-TENDER-BOND",
        "EV-GENERIC-SUBMIT",
        "EV-GENERIC-PAYMENT",
        "EV-PERFORMANCE-BOND",
        "EV-OTHER",
    ]
    assert merged[0].protected_baseline_anchor is True
    assert merged[3].protected_baseline_anchor is True
    assert all(item.promoted_by_coverage is False for item in merged)


def test_anchor_preserving_coverage_replaces_only_unprotected_tail() -> None:
    original = "投标担保金额和缴纳要求是什么？"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "anchor_preserving_direct_alignment"
        ),
    )
    baseline_anchor = _block(
        "EV-BASELINE-ANCHOR",
        "投标担保采用银行保函。",
        0,
    )
    protected_relation = _block(
        "EV-PROTECTED-RELATION",
        "缴纳投标担保须提交银行保函。",
        1,
    )
    generic_one = _block("EV-GENERIC-ONE", "其他说明一。", 2)
    generic_two = _block("EV-GENERIC-TWO", "其他说明二。", 3)
    generic_tail = _block("EV-GENERIC-TAIL", "其他说明尾部。", 4)
    direct_amount = _block(
        "EV-DIRECT-AMOUNT",
        "投标担保金额为人民币200,000.00元。",
        5,
    )
    ranked_results = [
        (
            original,
            [
                baseline_anchor,
                protected_relation,
                generic_one,
                generic_two,
                generic_tail,
                direct_amount,
            ],
        )
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    baseline_ids = [item.block.evidence_id for item in baseline]
    candidate_ids = [item.block.evidence_id for item in candidate]

    assert baseline_ids == [
        "EV-BASELINE-ANCHOR",
        "EV-PROTECTED-RELATION",
        "EV-GENERIC-ONE",
        "EV-GENERIC-TWO",
        "EV-GENERIC-TAIL",
    ]
    assert candidate_ids == [
        "EV-BASELINE-ANCHOR",
        "EV-PROTECTED-RELATION",
        "EV-GENERIC-ONE",
        "EV-GENERIC-TWO",
        "EV-DIRECT-AMOUNT",
    ]
    assert candidate_ids[:-1] == baseline_ids[:-1]
    assert candidate[0].protected_baseline_anchor is True
    assert candidate[1].protected_baseline_anchor is True
    assert candidate[4].selected_by_coverage is True
    assert candidate[4].promoted_by_coverage is True
    assert sum(item.promoted_by_coverage for item in candidate) == 1


def test_predicate_aware_plan_expands_shared_time_predicate() -> None:
    original = (
        "方案设计、方案定稿、深化设计和施工图设计的"
        "时间节点分别是什么？"
    )
    baseline = plan_tender_query(original)
    candidate = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )

    assert candidate.queries == baseline.queries
    assert candidate.coverage_need_subjects == (
        "方案设计",
        "方案定稿",
        "深化设计",
        "施工图设计",
    )
    assert candidate.coverage_need_types == (
        "time",
        "time",
        "time",
        "time",
    )
    assert candidate.coverage_need_answer_shapes == (
        "time_value",
        "time_value",
        "time_value",
        "time_value",
    )
    assert candidate.coverage_relation_shape_supported is True
    assert candidate.to_payload()["query_count"] == len(
        baseline.queries
    )


def test_predicate_aware_plan_requires_composite_deliverable_shape() -> None:
    plan = plan_tender_query(
        "方案设计、深化设计、施工图设计和竣工图分别要"
        "提交什么文件、几份以及哪些电子格式？",
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )

    assert plan.coverage_need_subjects == (
        "方案设计",
        "深化设计",
        "施工图设计",
        "竣工图",
    )
    assert plan.coverage_need_types == (
        "deliverable",
        "deliverable",
        "deliverable",
        "deliverable",
    )
    assert set(plan.coverage_need_answer_shapes) == {
        "deliverable+quantity+format"
    }


def test_predicate_aware_plan_recognizes_general_relation_shapes() -> None:
    cases = (
        (
            "清单版本与答疑版本是否存在冲突，哪些变更未同步？",
            "conflict",
            ("清单版本", "答疑版本", "变更"),
        ),
        (
            "设备参数和材料参数是否缺失，分别应补充哪些附件？",
            "missing",
            ("设备参数", "材料参数"),
        ),
        (
            "精装修单位负责哪些机电工作，哪些专业由其他单位"
            "负责但需要精装修单位配合？",
            "responsibility",
            ("精装修单位", "精装修单位"),
        ),
    )

    for question, expected_type, expected_subjects in cases:
        plan = plan_tender_query(
            question,
            enable_candidate_coverage_selection=True,
            candidate_coverage_selection_policy=(
                "predicate_aware_marginal_gain"
            ),
        )

        assert plan.coverage_relation_shape_supported is True
        assert set(plan.coverage_need_types) == {expected_type}
        assert plan.coverage_need_subjects == expected_subjects

    responsibility_plan = plan_tender_query(
        cases[-1][0],
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    assert responsibility_plan.coverage_need_answer_shapes == (
        "responsibility_relation",
        "responsibility_relation+collaboration",
    )


def test_predicate_aware_document_roles_alone_fail_closed() -> None:
    plan = plan_tender_query(
        "工程量清单和施工合同分别有哪些要求？",
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )

    assert plan.coverage_need_queries == ()
    assert plan.coverage_relation_shape_supported is False
    assert (
        plan.coverage_relation_shape_reason
        == "unsupported_relation_shape"
    )


def test_predicate_aware_keeps_five_distinct_fact_needs() -> None:
    plan = plan_tender_query(
        "投标截止时间、开标时间和地点、投标保证金要求，以及"
        "商务标份数分别是什么？",
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )

    assert plan.coverage_need_subjects == (
        "投标截止",
        "开标",
        "地点",
        "投标保证金",
        "商务标",
    )
    assert plan.coverage_need_types == (
        "time",
        "time",
        "location",
        "requirement",
        "quantity",
    )


def test_predicate_aware_unsupported_relation_is_exact_baseline() -> None:
    original = "第十一条付款安排对现金流有哪些不利影响？"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    ranked_results = [
        (
            original,
            [
                _block(f"EV-RISK-{index}", f"风险说明{index}", index)
                for index in range(1, 7)
            ],
        )
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=5,
    )

    assert candidate_plan.coverage_need_queries == ()
    assert candidate_plan.coverage_relation_shape_supported is False
    assert (
        candidate_plan.coverage_relation_shape_reason
        == "unsupported_relation_shape"
    )
    assert [item.block.evidence_id for item in candidate] == [
        item.block.evidence_id for item in baseline
    ]
    assert sum(item.promoted_by_coverage for item in candidate) == 0


def test_predicate_aware_subject_only_evidence_has_zero_promotion() -> None:
    original = "方案甲、方案乙的时间节点分别是什么？"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    ranked_results = [
        (
            original,
            [
                _block("EV-SUBJECT-A", "方案甲时间节点说明", 1),
                _block("EV-GENERIC-2", "其他说明二", 2),
                _block("EV-GENERIC-3", "其他说明三", 3),
                _block("EV-GENERIC-4", "其他说明四", 4),
                _block("EV-GENERIC-5", "其他说明五", 5),
                _block("EV-SUBJECT-B", "方案乙节点安排", 6),
            ],
        )
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=5,
    )

    assert [item.block.evidence_id for item in candidate] == [
        item.block.evidence_id for item in baseline
    ]
    assert sum(item.promoted_by_coverage for item in candidate) == 0


def test_predicate_aware_repeated_coverage_has_zero_promotion() -> None:
    original = "方案甲、方案乙的时间节点分别是什么？"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    ranked_results = [
        (
            original,
            [
                _block("EV-TIME-A", "方案甲在10日内完成。", 1),
                _block("EV-GENERIC-2", "其他说明二", 2),
                _block("EV-GENERIC-3", "其他说明三", 3),
                _block("EV-GENERIC-4", "其他说明四", 4),
                _block("EV-GENERIC-5", "其他说明五", 5),
                _block(
                    "EV-TIME-A-DUPLICATE",
                    "方案甲在合同签订后10日内完成。",
                    6,
                ),
            ],
        )
    ]
    candidate_pool_order = [
        block.evidence_id for block in ranked_results[0][1]
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=5,
    )

    assert [item.block.evidence_id for item in candidate] == [
        item.block.evidence_id for item in baseline
    ]
    assert sum(item.promoted_by_coverage for item in candidate) == 0
    assert [
        block.evidence_id for block in ranked_results[0][1]
    ] == candidate_pool_order


def test_predicate_aware_positive_gain_replaces_only_tail() -> None:
    original = "方案甲、方案乙的时间节点分别是什么？"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    ranked_results = [
        (
            original,
            [
                _block(
                    "EV-TIME-A",
                    "方案甲在合同签订后10个工作日完成。",
                    1,
                ),
                _block("EV-GENERIC-2", "其他说明二", 2),
                _block("EV-GENERIC-3", "其他说明三", 3),
                _block("EV-GENERIC-4", "其他说明四", 4),
                _block("EV-GENERIC-TAIL", "其他说明尾部", 5),
                _block(
                    "EV-TIME-B",
                    "方案乙在评审通过后5个工作日完成。",
                    6,
                ),
            ],
        )
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    baseline_ids = [item.block.evidence_id for item in baseline]
    candidate_ids = [item.block.evidence_id for item in candidate]

    assert candidate_ids[:-1] == baseline_ids[:-1]
    assert candidate_ids[-1] == "EV-TIME-B"
    assert candidate[0].protected_baseline_anchor is True
    assert candidate[-1].promoted_by_coverage is True
    assert candidate[-1].replacement_evidence_id == "EV-GENERIC-TAIL"
    assert candidate[-1].replacement_position == 5
    assert candidate[-1].promotion_sequence == 1
    assert candidate[-1].net_coverage_gain == 1
    assert candidate[-1].coverage_before_count == 1
    assert candidate[-1].coverage_after_count == 2
    assert candidate[-1].coverage_before_need_indexes == (0,)
    assert candidate[-1].coverage_after_need_indexes == (0, 1)
    assert candidate[-1].added_need_indexes == (1,)
    assert candidate[-1].query_diversity_preserved is True


def test_predicate_aware_rejects_victim_exclusive_fact_loss() -> None:
    original = "方案甲、方案乙、方案丙的时间节点分别是什么？"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    ranked_results = [
        (
            original,
            [
                _block("EV-TIME-A", "方案甲在10日内完成。", 1),
                _block("EV-TIME-B", "方案乙在8日内完成。", 2),
                _block("EV-TIME-C", "方案丙在6日内完成。", 3),
            ],
        )
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=2,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=2,
    )

    assert [item.block.evidence_id for item in candidate] == [
        item.block.evidence_id for item in baseline
    ]
    assert sum(item.promoted_by_coverage for item in candidate) == 0


def test_predicate_aware_allows_victim_fact_preserving_replacement() -> None:
    original = "方案甲、方案乙、方案丙的时间节点分别是什么？"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    ranked_results = [
        (
            original,
            [
                _block("EV-TIME-A", "方案甲在10日内完成。", 1),
                _block("EV-TIME-B", "方案乙在8日内完成。", 2),
                _block(
                    "EV-TIME-BC",
                    "方案乙在8日内完成，方案丙在6日内完成。",
                    3,
                ),
            ],
        )
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=2,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=2,
    )

    assert [item.block.evidence_id for item in baseline] == [
        "EV-TIME-A",
        "EV-TIME-B",
    ]
    assert [item.block.evidence_id for item in candidate] == [
        "EV-TIME-A",
        "EV-TIME-BC",
    ]
    assert candidate[1].victim_exclusive_need_indexes == (1,)
    assert candidate[1].net_coverage_gain == 1


def test_predicate_aware_preserves_all_unique_auxiliary_anchors() -> None:
    original = "请分别说明付款条件、工期要求和质量验收标准。"
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    shared = _block("EV-SHARED", "综合资料索引", 1)
    payment = _block("EV-AUX-PAYMENT", "付款条件章节标题", 2)
    schedule = _block("EV-AUX-SCHEDULE", "工期要求章节标题", 3)
    quality = _block("EV-AUX-QUALITY", "质量验收章节标题", 4)
    late_answer = _block(
        "EV-LATE-PAYMENT",
        "付款条件为预付款15%，进度款支付80%。",
        5,
    )
    ranked_results = [
        (baseline_plan.queries[0], [shared, late_answer]),
        (baseline_plan.queries[1], [payment, shared]),
        (baseline_plan.queries[2], [schedule, shared]),
        (baseline_plan.queries[3], [quality, shared]),
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=3,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=3,
    )

    assert [item.block.evidence_id for item in candidate] == [
        item.block.evidence_id for item in baseline
    ]
    assert all(item.protected_baseline_anchor for item in candidate)
    assert sum(item.promoted_by_coverage for item in candidate) == 0


def test_predicate_aware_caps_promotions_at_two() -> None:
    original = (
        "方案甲、方案乙、方案丙、方案丁的时间节点分别是什么？"
    )
    baseline_plan = plan_tender_query(original)
    candidate_plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )
    ranked_results = [
        (
            original,
            [
                _block("EV-TOP", "综合资料索引", 1),
                _block("EV-GENERIC-2", "其他说明二", 2),
                _block("EV-GENERIC-3", "其他说明三", 3),
                _block("EV-GENERIC-4", "其他说明四", 4),
                _block("EV-GENERIC-5", "其他说明五", 5),
                _block("EV-TIME-A", "方案甲在10日内完成。", 6),
                _block("EV-TIME-B", "方案乙在8日内完成。", 7),
                _block("EV-TIME-C", "方案丙在6日内完成。", 8),
            ],
        )
    ]

    baseline = merge_planned_results(
        plan=baseline_plan,
        ranked_results=ranked_results,
        top_k=5,
    )
    candidate = merge_planned_results(
        plan=candidate_plan,
        ranked_results=ranked_results,
        top_k=5,
    )

    assert [item.block.evidence_id for item in candidate][0] == (
        [item.block.evidence_id for item in baseline][0]
    )
    assert sum(item.promoted_by_coverage for item in candidate) == 2
    assert sorted(
        item.promotion_sequence
        for item in candidate
        if item.promoted_by_coverage
    ) == [1, 2]


def test_query_planner_rejects_unknown_coverage_policy() -> None:
    try:
        plan_tender_query(
            "投标担保金额和缴纳要求是什么？",
            enable_candidate_coverage_selection=True,
            candidate_coverage_selection_policy="project_specific",
        )
    except ValueError as exc:
        assert "unsupported candidate coverage selection policy" in str(
            exc
        )
    else:
        raise AssertionError("unknown policy must be rejected")


def test_retrieval_router_uses_exact_for_fact_and_identifier_queries() -> None:
    fact = route_tender_query("投标保证金是多少？")
    clause = route_tender_query("第12.3条的付款比例")

    assert fact.mode == "exact"
    assert fact.exact_signals == ()
    assert clause.mode == "exact"
    assert "clause_reference" in clause.exact_signals


def test_retrieval_router_uses_semantic_for_risk_analysis() -> None:
    route = route_tender_query("付款条件对乙方有哪些不利风险？")

    assert route.mode == "semantic"
    assert "risk_analysis" in route.semantic_signals
    assert route.fallback_mode == "hybrid"


def test_retrieval_router_only_uses_primary_hybrid_for_mixed_intent() -> None:
    route = route_tender_query("第12.3条延期责任对乙方有什么风险？")

    assert route.mode == "hybrid"
    assert "clause_reference" in route.exact_signals
    assert "risk_analysis" in route.semantic_signals
    assert route.fallback_mode is None


class _QueryPlanRepository:
    def __init__(
        self,
        results: dict[str, list[EvidenceBlock]],
        *,
        contexts: dict[str, list[EvidenceBlock]] | None = None,
        structural_parent: EvidenceBlock | None = None,
    ) -> None:
        self.results = results
        self.contexts = contexts or {}
        self.structural_parent = structural_parent
        self.calls: list[tuple[str, int, str]] = []
        self.context_calls: list[tuple[str, int, int]] = []

    def search(
        self,
        *,
        case_id: str,
        query: str,
        top_k: int,
        search_mode: str = "hybrid",
    ) -> list[EvidenceBlock]:
        assert case_id == "CASE-QUERY-PLAN"
        self.calls.append((query, top_k, search_mode))
        return self.results.get(query, [])[:top_k]

    def get_context(
        self,
        *,
        case_id: str,
        evidence_id: str,
        before_blocks: int,
        after_blocks: int,
    ) -> list[EvidenceBlock]:
        assert case_id == "CASE-QUERY-PLAN"
        self.context_calls.append(
            (evidence_id, before_blocks, after_blocks)
        )
        return self.contexts.get(evidence_id, [])

    def get_structural_context(
        self,
        *,
        case_id: str,
        evidence_ids: list[str],
        max_heading_lookback: int = 12,
    ) -> dict[str, list[EvidenceStructuralContext]]:
        assert case_id == "CASE-QUERY-PLAN"
        assert max_heading_lookback == 12
        if self.structural_parent is None:
            return {}
        return {
            evidence_id: [
                EvidenceStructuralContext(
                    relation="table_header_parent",
                    content=self.structural_parent.content,
                    evidence_ref=self.structural_parent.to_ref(
                        context_read=False,
                        quote=self.structural_parent.content,
                    ),
                )
            ]
            for evidence_id in evidence_ids
        }


def test_mcp_search_executes_query_plan_and_returns_auditable_plan() -> None:
    original = "项目的付款条件、工期风险和投标保证金分别是什么？"
    plan = plan_tender_query(original)
    repository = _QueryPlanRepository(
        {
            plan.queries[0]: [_block("EV-SHARED", "综合说明", 0)],
            plan.queries[1]: [_block("EV-PAYMENT", "付款条件", 1)],
            plan.queries[2]: [_block("EV-SCHEDULE", "工期风险", 2)],
            plan.queries[3]: [_block("EV-BOND", "投标保证金", 3)],
        }
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=3,
    )

    assert result.status == ResultStatus.OK
    assert [item[0] for item in repository.calls] == list(plan.queries)
    assert all(item[1] == 5 for item in repository.calls)
    assert [item[2] for item in repository.calls] == [
        "semantic",
        "exact",
        "exact",
        "exact",
    ]
    assert result.data["query_plan"]["strategy"] == "topic_decomposition"
    assert result.data["query_plan"]["query_count"] == 4
    assert result.data["query_plan"]["per_query_candidate_top_k"] == 5
    assert result.data["query_plan"]["final_top_k"] == 3
    assert result.data["query_plan"]["routing_summary"]["requested"] == {
        "exact": 3,
        "semantic": 1,
        "hybrid": 0,
    }
    assert [
        item["requested_mode"]
        for item in result.data["query_plan"]["query_tasks"]
    ] == ["semantic", "exact", "exact", "exact"]
    assert [
        item["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
    ] == ["EV-PAYMENT", "EV-SCHEDULE", "EV-BOND"]
    assert result.data["matches"][0]["matched_queries"] == [
        plan.queries[1]
    ]


def test_mcp_search_decouples_candidate_depth_from_final_top_k() -> None:
    original = "项目的付款条件、工期风险和投标保证金分别是什么？"
    plan = plan_tender_query(original)
    repository = _QueryPlanRepository(
        {
            query: [
                _block(f"EV-{index}-{rank}", query, rank)
                for rank in range(20)
            ]
            for index, query in enumerate(plan.queries)
        }
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=5,
    )

    assert result.status == ResultStatus.OK
    assert all(item[1] == 20 for item in repository.calls)
    assert len(result.data["matches"]) == 5
    assert result.data["query_plan"]["per_query_candidate_top_k"] == 20
    assert result.data["query_plan"]["final_top_k"] == 5


def test_mcp_search_executes_fact_companion_without_changing_primary_count():
    original = "第十一条付款安排对现金流有哪些不利影响？"
    plan = plan_tender_query(
        original,
        enable_semantic_fact_companion=True,
    )
    supporting_query = plan.supporting_queries[0]
    repository = _QueryPlanRepository(
        {
            original: [_block("EV-RISK-DECOY", "风险说明", 0)],
            supporting_query: [
                _block("EV-PAYMENT-FACT", "付款事实", 1)
            ],
        }
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_semantic_fact_companion=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=5,
    )

    assert result.status == ResultStatus.OK
    assert [item[0] for item in repository.calls] == [
        original,
        supporting_query,
    ]
    assert [item[1] for item in repository.calls] == [20, 20]
    assert [item[2] for item in repository.calls] == [
        "hybrid",
        "exact",
    ]
    query_plan = result.data["query_plan"]
    assert query_plan["query_count"] == 1
    assert query_plan["supporting_query_count"] == 1
    assert len(query_plan["query_tasks"]) == 1
    assert len(query_plan["supporting_query_tasks"]) == 1
    assert (
        query_plan["supporting_query_tasks"][0]["query_kind"]
        == "supporting_fact"
    )
    assert "EV-PAYMENT-FACT" in {
        item["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
    }


def test_mcp_search_executes_fact_slots_without_changing_primary_count():
    original = (
        "投标文件要递交到哪里，需要准备几套纸质文件和几份电子文件？"
    )
    plan = plan_tender_query(
        original,
        enable_atomic_fact_slots=True,
    )
    repository = _QueryPlanRepository(
        {
            original: [_block("EV-ORIGINAL", "综合说明", 0)],
            plan.fact_slot_queries[0]: [
                _block("EV-LOCATION", "递交地点", 1)
            ],
            plan.fact_slot_queries[1]: [
                _block("EV-PAPER", "纸质文件份数", 2)
            ],
            plan.fact_slot_queries[2]: [
                _block("EV-ELECTRONIC", "电子文件份数", 3)
            ],
        }
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_atomic_fact_slots=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=5,
    )

    assert result.status == ResultStatus.OK
    assert [item[0] for item in repository.calls] == [
        original,
        *plan.fact_slot_queries,
    ]
    query_plan = result.data["query_plan"]
    assert query_plan["query_count"] == 1
    assert query_plan["fact_slot_query_count"] == 3
    assert len(query_plan["query_tasks"]) == 1
    assert len(query_plan["fact_slot_query_tasks"]) == 3
    assert {
        item["query_kind"]
        for item in query_plan["fact_slot_query_tasks"]
    } == {"atomic_fact_slot"}
    assert {
        item["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
    } >= {"EV-LOCATION", "EV-PAPER", "EV-ELECTRONIC"}


def test_anchor_preserving_candidate_keeps_search_calls_unchanged() -> None:
    original = "请分别说明付款条件、工期风险和质量验收标准。"
    plan = plan_tender_query(original)
    shared = [
        _block(
            f"EV-CALL-SHARED-{index}",
            f"综合资料索引{index}",
            index,
        )
        for index in range(1, 6)
    ]
    results = {
        plan.queries[0]: shared,
        plan.queries[1]: [
            _block("EV-CALL-AUX-PAYMENT", "辅助检索结果甲", 10),
            *shared,
        ],
        plan.queries[2]: [
            _block("EV-CALL-AUX-SCHEDULE", "辅助检索结果乙", 11),
            *shared,
        ],
        plan.queries[3]: [
            _block("EV-CALL-AUX-QUALITY", "辅助检索结果丙", 12),
            *shared,
        ],
    }
    baseline_repository = _QueryPlanRepository(results)
    candidate_repository = _QueryPlanRepository(results)
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    baseline_service = TenderEvidenceService(
        baseline_repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
    )
    candidate_service = TenderEvidenceService(
        candidate_repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "anchor_preserving_direct_alignment"
        ),
    )

    baseline = baseline_service.search_tender_evidence(
        query=original,
        top_k=5,
    )
    candidate = candidate_service.search_tender_evidence(
        query=original,
        top_k=5,
    )
    baseline_ids = [
        item["evidence_ref"]["evidence_id"]
        for item in baseline.data["matches"]
    ]
    candidate_ids = [
        item["evidence_ref"]["evidence_id"]
        for item in candidate.data["matches"]
    ]

    assert baseline.status == ResultStatus.OK
    assert candidate.status == ResultStatus.OK
    assert candidate_repository.calls == baseline_repository.calls
    assert len(candidate_repository.calls) == len(plan.queries)
    assert candidate_ids == baseline_ids
    assert candidate.data["query_plan"]["coverage_selection_summary"][
        "promoted_evidence_count"
    ] == 0
    assert candidate.data["query_plan"][
        "total_search_query_count"
    ] == baseline.data["query_plan"]["total_search_query_count"]


def test_sufficiency_assessment_keeps_baseline_results_and_calls() -> None:
    original = "方案甲、方案乙的时间节点分别是什么？"
    results = {
        original: [
            _block("EV-SUFFICIENCY-A", "方案甲在10个工作日内完成。", 1),
            _block("EV-SUFFICIENCY-B", "方案乙在6个工作日内完成。", 2),
            _block("EV-SUFFICIENCY-3", "综合说明三", 3),
            _block("EV-SUFFICIENCY-4", "综合说明四", 4),
            _block("EV-SUFFICIENCY-5", "综合说明五", 5),
            _block("EV-SUFFICIENCY-6", "综合说明六", 6),
        ]
    }
    baseline_repository = _QueryPlanRepository(results)
    assessment_repository = _QueryPlanRepository(results)
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    baseline_service = TenderEvidenceService(
        baseline_repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
    )
    assessment_service = TenderEvidenceService(
        assessment_repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_evidence_sufficiency_assessment=True,
    )

    baseline = baseline_service.search_tender_evidence(
        query=original,
        top_k=5,
    )
    assessment = assessment_service.search_tender_evidence(
        query=original,
        top_k=5,
    )
    baseline_ids = [
        item["evidence_ref"]["evidence_id"]
        for item in baseline.data["matches"]
    ]
    assessment_ids = [
        item["evidence_ref"]["evidence_id"]
        for item in assessment.data["matches"]
    ]
    summary = assessment.data["query_plan"][
        "evidence_sufficiency_summary"
    ]

    assert baseline.status == ResultStatus.OK
    assert assessment.status == ResultStatus.OK
    assert assessment_repository.calls == baseline_repository.calls
    assert assessment_repository.context_calls == []
    assert baseline_ids == assessment_ids
    assert assessment.data["query_plan"][
        "coverage_selection_policy"
    ] == "off"
    assert assessment.data["query_plan"][
        "coverage_selection_summary"
    ]["promoted_evidence_count"] == 0
    assert assessment.data["query_plan"][
        "total_search_query_count"
    ] == baseline.data["query_plan"]["total_search_query_count"]
    assert summary == {
        "enabled": True,
        "strategy": "predicate_aware_relation_evidence_v1",
        "relation_shape_supported": True,
        "relation_shape_reason": None,
        "required_need_count": 2,
        "covered_need_count": 2,
        "covered_need_indexes": [0, 1],
        "sufficiency_status": "candidate_sufficient",
        "decision_reason_codes": [
            "all_relation_needs_directly_covered"
        ],
        "changes_result_selection": False,
        "additional_search_query_count": 0,
    }


def test_sufficiency_assessment_marks_partial_baseline_insufficient() -> None:
    original = "方案甲、方案乙的时间节点分别是什么？"
    results = {
        original: [
            _block("EV-SUFFICIENCY-ONLY-A", "方案甲在10个工作日内完成。", 1),
            _block("EV-SUFFICIENCY-WEAK-B", "方案乙相关工作另行说明。", 2),
            _block("EV-SUFFICIENCY-PARTIAL-3", "综合说明三", 3),
            _block("EV-SUFFICIENCY-PARTIAL-4", "综合说明四", 4),
            _block("EV-SUFFICIENCY-PARTIAL-5", "综合说明五", 5),
        ]
    }
    repository = _QueryPlanRepository(results)
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        enable_evidence_sufficiency_assessment=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=5,
    )
    summary = result.data["query_plan"][
        "evidence_sufficiency_summary"
    ]

    assert summary["required_need_count"] == 2
    assert summary["covered_need_count"] == 1
    assert summary["covered_need_indexes"] == [0]
    assert summary["sufficiency_status"] == "insufficient"
    assert summary["decision_reason_codes"] == [
        "partial_relation_need_coverage"
    ]


def test_predicate_aware_candidate_keeps_calls_and_audits_gain() -> None:
    original = "方案甲、方案乙的时间节点分别是什么？"
    results = {
        original: [
            _block("EV-TIME-A", "方案甲在10个工作日内完成。", 1),
            _block("EV-GENERIC-2", "其他说明二", 2),
            _block("EV-GENERIC-3", "其他说明三", 3),
            _block("EV-GENERIC-4", "其他说明四", 4),
            _block("EV-GENERIC-TAIL", "其他说明尾部", 5),
            _block("EV-TIME-B", "方案乙在6个工作日内完成。", 6),
        ]
    }
    baseline_repository = _QueryPlanRepository(results)
    candidate_repository = _QueryPlanRepository(results)
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    baseline_service = TenderEvidenceService(
        baseline_repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
    )
    candidate_service = TenderEvidenceService(
        candidate_repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_candidate_coverage_selection=True,
        candidate_coverage_selection_policy=(
            "predicate_aware_marginal_gain"
        ),
    )

    baseline = baseline_service.search_tender_evidence(
        query=original,
        top_k=5,
    )
    candidate = candidate_service.search_tender_evidence(
        query=original,
        top_k=5,
    )
    baseline_ids = [
        item["evidence_ref"]["evidence_id"]
        for item in baseline.data["matches"]
    ]
    candidate_ids = [
        item["evidence_ref"]["evidence_id"]
        for item in candidate.data["matches"]
    ]
    summary = candidate.data["query_plan"][
        "coverage_selection_summary"
    ]

    assert baseline.status == ResultStatus.OK
    assert candidate.status == ResultStatus.OK
    assert candidate_repository.calls == baseline_repository.calls
    assert len(candidate_repository.calls) == 1
    assert baseline_repository.context_calls == []
    assert candidate_repository.context_calls == []
    assert candidate.data["query_plan"][
        "total_search_query_count"
    ] == baseline.data["query_plan"]["total_search_query_count"]
    assert candidate_ids[:-1] == baseline_ids[:-1]
    assert candidate_ids[-1] == "EV-TIME-B"
    assert summary["promoted_evidence_count"] == 1
    assert summary["baseline_covered_need_count"] == 1
    assert summary["baseline_covered_need_indexes"] == [0]
    assert summary["final_covered_need_count"] == 2
    assert summary["final_covered_need_indexes"] == [0, 1]
    assert summary["total_net_coverage_gain"] == 1
    assert summary["query_diversity_preserved"] is True
    assert summary["decision_reason_codes"] == [
        "positive_net_gain_promotion"
    ]
    assert summary["promotion_audits"] == [
        {
            "promoted_evidence_id": "EV-TIME-B",
            "replacement_evidence_id": "EV-GENERIC-TAIL",
            "replacement_position": 5,
            "promotion_sequence": 1,
            "coverage_before_count": 1,
            "coverage_after_count": 2,
            "coverage_before_need_indexes": [0],
            "coverage_after_need_indexes": [0, 1],
            "added_need_indexes": [1],
            "net_coverage_gain": 1,
            "victim_exclusive_need_indexes": [],
            "query_diversity_preserved": True,
        }
    ]


def test_mcp_coverage_selection_adds_no_retrieval_queries() -> None:
    original = "请分别说明付款条件、工期要求和质量验收标准。"
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )
    payment = _block(
        "EV-PAYMENT",
        "付款方式：预付款15%，每月支付进度款80%。",
        0,
    )
    schedule_heading = _block("EV-SCHEDULE-HEADING", "工期要求：", 1)
    schedule = _block(
        "EV-SCHEDULE",
        "绝对工期100日历天，计划2026年5月10日开工。",
        2,
    )
    quality = _block(
        "EV-QUALITY",
        "达到国家施工质量验收规范要求，一次性验收合格。",
        3,
    )
    repository = _QueryPlanRepository(
        {
            plan.queries[0]: [_block("EV-ORIGINAL", "综合说明", 4)],
            plan.queries[1]: [payment],
            plan.queries[2]: [schedule_heading, schedule],
            plan.queries[3]: [quality],
        }
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_candidate_coverage_selection=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=5,
    )

    assert result.status == ResultStatus.OK
    assert [item[0] for item in repository.calls] == list(plan.queries)
    query_plan = result.data["query_plan"]
    assert query_plan["fact_slot_query_count"] == 0
    assert query_plan["coverage_need_count"] == 3
    assert query_plan["coverage_selection_summary"] == {
        "enabled": True,
        "policy": "greedy",
        "need_count": 3,
        "covered_need_count": 3,
        "selected_evidence_count": 3,
        "protected_baseline_anchor_count": 0,
        "promoted_evidence_count": 0,
    }
    assert {
        item["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
    } >= {"EV-PAYMENT", "EV-SCHEDULE", "EV-QUALITY"}


def test_controlled_second_round_only_retries_uncovered_partial_fact() -> None:
    original = (
        "方案设计、深化设计、施工图设计和竣工图分别要提交什么文件、"
        "几份以及哪些电子格式？"
    )
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )
    assert len(plan.queries) == 1
    assert len(plan.coverage_need_queries) == 4
    initial = [
        _block(
            "EV-SCHEME",
            "方案设计提交方案文本2份及PDF电子文件。",
            0,
        ),
        _block(
            "EV-DEEPEN",
            "深化设计提交深化图册2份及DWG电子文件。",
            1,
        ),
        _block(
            "EV-CONSTRUCTION",
            "施工图设计提交施工图8份及DWG电子文件。",
            2,
        ),
    ]
    completion = _block(
        "EV-AS-BUILT",
        (
            f"{plan.coverage_need_queries[3]}："
            "竣工图提交蓝图4份及DWG、PDF电子文件。"
        ),
        3,
    )
    table_header = _block(
        "EV-TABLE-HEADER",
        "阶段 | 成果文件 | 份数",
        4,
    )
    repository = _QueryPlanRepository(
        {
            plan.queries[0]: initial,
            plan.coverage_need_queries[3]: [completion],
        },
        structural_parent=table_header,
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_candidate_coverage_selection=True,
        enable_structured_context_groups=True,
        enable_controlled_second_round=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=5,
    )

    assert result.status == ResultStatus.OK
    query_plan = result.data["query_plan"]
    assert [item[0] for item in repository.calls] == [
        plan.queries[0],
        plan.coverage_need_queries[3],
    ]
    assert query_plan["controlled_retry_summary"] == {
        "enabled": True,
        "triggered": True,
        "trigger_policy": "partial_coverage_only",
        "max_round_count": 1,
        "max_retry_query_count": 2,
        "required_need_count": 4,
        "initial_covered_need_count": 3,
        "initial_uncovered_need_count": 1,
        "executed_retry_query_count": 1,
        "new_candidate_count": 1,
        "final_covered_need_count": 4,
        "remaining_uncovered_need_count": 0,
        "skip_reason": None,
        "integration_policy": "preserve_first_round_anchors",
        "preferred_parent_count": 1,
        "retry_structural_context_summary": {
            "enabled": True,
            "candidate_count": 1,
            "contextualized_candidate_count": 1,
            "parent_count": 1,
            "section_parent_count": 0,
            "table_header_parent_count": 1,
            "sheet_header_parent_count": 0,
            "lookup_count": 1,
            "error_count": 0,
        },
    }
    assert query_plan["total_search_query_count"] == 2
    assert query_plan["structured_sibling_group_summary"][
        "preferred_parent_applied"
    ] is True
    assert (
        query_plan["controlled_retry_query_tasks"][0][
            "coverage_need_index"
        ]
        == 3
    )
    anchor_ids = {
        item["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
    }
    group_ids = {
        member["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
        for member in item["context_evidence_group"]["members"]
    }
    assert anchor_ids == {
        "EV-SCHEME",
        "EV-DEEPEN",
        "EV-CONSTRUCTION",
    }
    assert group_ids >= {
        "EV-AS-BUILT",
    }


def test_controlled_second_round_skips_zero_coverage_negative_case() -> None:
    original = "资料是否明确设计费付款比例、付款节点和结算条件？"
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )
    repository = _QueryPlanRepository(
        {query: [] for query in plan.queries}
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        enable_candidate_coverage_selection=True,
        enable_structured_context_groups=True,
        enable_controlled_second_round=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=5,
    )

    assert result.status == ResultStatus.NO_RESULT
    assert {
        item[0] for item in repository.calls
    } == set(plan.queries)
    summary = result.data["query_plan"][
        "controlled_retry_summary"
    ]
    assert summary["triggered"] is False
    assert summary["skip_reason"] == "zero_coverage_safety_guard"
    assert summary["executed_retry_query_count"] == 0


def test_merge_uses_adjacent_context_only_as_coverage_candidate() -> None:
    original = "投标担保金额和缴纳要求是什么？"
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )
    amount = _block(
        "EV-AMOUNT",
        "投标担保金额为人民币200,000.00元。",
        10,
        section="投标担保",
    )
    requirement = _block(
        "EV-REQUIREMENT",
        "缴纳要求：必须在投标截止前提交银行保函。",
        11,
        section="投标担保",
    )

    merged = merge_planned_results(
        plan=plan,
        ranked_results=[(original, [amount])],
        supplemental_blocks=[requirement],
        top_k=2,
    )

    assert [item.block.evidence_id for item in merged] == [
        "EV-AMOUNT",
        "EV-REQUIREMENT",
    ]
    assert merged[1].selected_by_coverage is True
    assert merged[1].candidate_sources == ("adjacent_context",)
    assert merged[1].matched_queries == ()


def test_mcp_adjacent_expansion_is_bounded_and_adds_no_search_query() -> None:
    original = "投标担保金额和缴纳要求是什么？"
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )
    amount = _block(
        "EV-AMOUNT",
        "投标担保金额为人民币200,000.00元。",
        10,
        section="投标担保",
    )
    requirement = _block(
        "EV-REQUIREMENT",
        "缴纳要求：必须在投标截止前提交银行保函。",
        11,
        section="投标担保",
    )
    repository = _QueryPlanRepository(
        {plan.queries[0]: [amount]},
        contexts={
            amount.evidence_id: [amount, requirement],
        },
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_candidate_coverage_selection=True,
        enable_adjacent_candidate_expansion=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=2,
    )

    assert result.status == ResultStatus.OK
    assert [item[0] for item in repository.calls] == list(plan.queries)
    assert repository.context_calls == [("EV-AMOUNT", 1, 1)]
    assert [
        item["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
    ] == ["EV-AMOUNT", "EV-REQUIREMENT"]
    assert result.data["matches"][1]["candidate_sources"] == [
        "adjacent_context"
    ]
    assert result.data["query_plan"]["adjacent_expansion_summary"] == {
        "enabled": True,
        "seed_count": 1,
        "context_read_count": 1,
        "context_block_count": 2,
        "added_candidate_count": 1,
        "existing_candidate_count": 0,
        "filtered_document_count": 0,
        "filtered_section_count": 0,
        "filtered_non_direct_count": 0,
        "error_count": 0,
    }


def test_mcp_adjacent_expansion_rejects_cross_section_and_far_blocks() -> None:
    original = "投标担保金额和缴纳要求是什么？"
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )
    amount = _block(
        "EV-AMOUNT",
        "投标担保金额为人民币200,000.00元。",
        10,
        section="投标担保",
    )
    cross_section = _block(
        "EV-CROSS-SECTION",
        "缴纳要求：必须提交银行保函。",
        11,
        section="其他条款",
    )
    far_block = _block(
        "EV-FAR",
        "缴纳要求：必须提交银行保函。",
        13,
        section="投标担保",
    )
    cross_document = _block(
        "EV-CROSS-DOCUMENT",
        "缴纳要求：必须提交银行保函。",
        9,
        section="投标担保",
        document_id="DOC-OTHER",
    )
    repository = _QueryPlanRepository(
        {plan.queries[0]: [amount]},
        contexts={
            amount.evidence_id: [
                amount,
                cross_section,
                far_block,
                cross_document,
            ],
        },
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        enable_candidate_coverage_selection=True,
        enable_adjacent_candidate_expansion=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=2,
    )

    assert result.status == ResultStatus.OK
    assert [
        item["evidence_ref"]["evidence_id"]
        for item in result.data["matches"]
    ] == ["EV-AMOUNT"]
    summary = result.data["query_plan"]["adjacent_expansion_summary"]
    assert summary["added_candidate_count"] == 0
    assert summary["filtered_document_count"] == 1
    assert summary["filtered_section_count"] == 1
    assert summary["filtered_non_direct_count"] == 1


def test_mcp_context_group_preserves_anchor_and_attaches_one_neighbor() -> None:
    original = (
        "投标文件要递交到哪里，需要准备几套纸质文件和几份电子文件？"
    )
    plan = plan_tender_query(
        original,
        enable_candidate_coverage_selection=True,
    )
    copies = _block(
        "EV-COPIES",
        "投标文件正本1套、副本1套，电子扫描件1份。",
        17,
        section="投标要求",
    )
    unrelated_before = _block(
        "EV-VALIDITY",
        "投标有效期为90天。",
        16,
        section="投标要求",
    )
    location = _block(
        "EV-LOCATION",
        "投标文件递交至产业促进中心3楼。",
        18,
        section="投标要求",
    )
    repository = _QueryPlanRepository(
        {plan.queries[0]: [copies]},
        contexts={
            copies.evidence_id: [
                unrelated_before,
                copies,
                location,
            ],
        },
    )
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
        per_query_candidate_top_k=20,
        enable_candidate_coverage_selection=True,
        enable_context_evidence_groups=True,
    )

    result = service.search_tender_evidence(
        query=original,
        top_k=1,
    )

    assert result.status == ResultStatus.OK
    assert [item[0] for item in repository.calls] == list(plan.queries)
    assert repository.context_calls == [("EV-COPIES", 1, 1)]
    assert len(result.data["matches"]) == 1
    match = result.data["matches"][0]
    assert match["evidence_ref"]["evidence_id"] == "EV-COPIES"
    group = match["context_evidence_group"]
    assert group["anchor_evidence_id"] == "EV-COPIES"
    assert group["member_count"] == 1
    assert group["members"][0]["evidence_ref"]["evidence_id"] == (
        "EV-LOCATION"
    )
    assert group["members"][0]["evidence_ref"]["context_read"] is False
    assert result.data["query_plan"][
        "context_evidence_group_summary"
    ] == {
        "enabled": True,
        "anchor_count": 1,
        "seed_count": 1,
        "context_read_count": 1,
        "context_block_count": 3,
        "grouped_anchor_count": 1,
        "member_count": 1,
        "existing_candidate_count": 0,
        "filtered_document_count": 0,
        "filtered_section_count": 0,
        "filtered_non_direct_count": 0,
        "filtered_no_coverage_count": 1,
        "error_count": 0,
        "max_members_per_anchor": 1,
    }


def test_mcp_rejects_two_adjacent_experiment_modes_together() -> None:
    repository = _QueryPlanRepository({})
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )

    try:
        TenderEvidenceService(
            repository,
            scope_provider=lambda: scope,
            enable_adjacent_candidate_expansion=True,
            enable_context_evidence_groups=True,
        )
    except ValueError as exc:
        assert "mutually exclusive" in str(exc)
    else:
        raise AssertionError("mutually exclusive modes must be rejected")


class _FallbackRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(
        self,
        *,
        case_id: str,
        query: str,
        top_k: int,
        search_mode: str = "hybrid",
    ) -> list[EvidenceBlock]:
        del case_id, query, top_k
        self.calls.append(search_mode)
        if search_mode == "hybrid":
            return [_block("EV-FALLBACK", "补充命中", 0)]
        return []


def test_mcp_search_escalates_empty_single_channel_to_hybrid() -> None:
    repository = _FallbackRepository()
    now = int(time.time())
    scope = TenderScope(
        case_id="CASE-QUERY-PLAN",
        assessment_id="ASSESSMENT-QUERY-PLAN",
        agent_run_id="RUN-QUERY-PLAN",
        subject="bid-intake-agent",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://tender.test/mcp",
        issuer="https://tender.test",
    )
    service = TenderEvidenceService(
        repository,
        scope_provider=lambda: scope,
    )

    result = service.search_tender_evidence(
        query="投标保证金是多少？",
        top_k=3,
    )

    assert result.status == ResultStatus.OK
    assert repository.calls == ["exact", "hybrid"]
    task = result.data["query_plan"]["query_tasks"][0]
    assert task["requested_mode"] == "exact"
    assert task["executed_mode"] == "hybrid"
    assert task["fallback_triggered"] is True
