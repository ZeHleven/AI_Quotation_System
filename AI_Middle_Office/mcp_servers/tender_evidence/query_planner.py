from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .contracts import EvidenceBlock


QUERY_PLAN_SCHEMA_VERSION = "tender-query-plan/v1"
DEFAULT_MAX_QUERY_COUNT = 6
DEFAULT_MAX_FACT_SLOT_COUNT = 4
QUERY_RRF_K = 60
ADJACENT_CONTEXT_COVERAGE_BONUS = 0.5
DEFAULT_COVERAGE_SELECTION_POLICY = "greedy"
ANCHOR_PRESERVING_COVERAGE_SELECTION_POLICY = (
    "anchor_preserving_direct_alignment"
)
PREDICATE_AWARE_MARGINAL_GAIN_POLICY = (
    "predicate_aware_marginal_gain"
)
PREDICATE_AWARE_SUFFICIENCY_ASSESSMENT = (
    "predicate_aware_relation_evidence_v1"
)
_COVERAGE_SELECTION_POLICIES = {
    DEFAULT_COVERAGE_SELECTION_POLICY,
    ANCHOR_PRESERVING_COVERAGE_SELECTION_POLICY,
    PREDICATE_AWARE_MARGINAL_GAIN_POLICY,
}
_MIN_DIRECT_ALIGNMENT_CHARS = 4
_MAX_DIRECT_ALIGNMENT_CHARS = 12
_MAX_MARGINAL_PROMOTIONS_PER_CASE = 2
_MAX_PREDICATE_AWARE_NEED_COUNT = 6
_MIN_PREDICATE_SUBJECT_ALIGNMENT = 0.75
_MIN_PREDICATE_COVERAGE_SCORE = 4.5


@dataclass(frozen=True)
class _TopicRule:
    topic: str
    query: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class TenderQueryPlan:
    original_query: str
    queries: tuple[str, ...]
    topics: tuple[str, ...]
    strategy: str
    supporting_queries: tuple[str, ...] = ()
    supporting_topics: tuple[str, ...] = ()
    supporting_strategy: str | None = None
    fact_slot_queries: tuple[str, ...] = ()
    fact_slot_types: tuple[str, ...] = ()
    fact_slot_strategy: str | None = None
    coverage_need_queries: tuple[str, ...] = ()
    coverage_need_types: tuple[str, ...] = ()
    coverage_need_subjects: tuple[str, ...] = ()
    coverage_need_answer_shapes: tuple[str, ...] = ()
    coverage_strategy: str | None = None
    coverage_selection_policy: str = "off"
    coverage_relation_shape_supported: bool | None = None
    coverage_relation_shape_reason: str | None = None
    sufficiency_need_queries: tuple[str, ...] = ()
    sufficiency_need_types: tuple[str, ...] = ()
    sufficiency_need_subjects: tuple[str, ...] = ()
    sufficiency_need_answer_shapes: tuple[str, ...] = ()
    sufficiency_strategy: str | None = None
    sufficiency_relation_shape_supported: bool | None = None
    sufficiency_relation_shape_reason: str | None = None

    @property
    def atomic_queries(self) -> tuple[str, ...]:
        return self.queries[1:]

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": QUERY_PLAN_SCHEMA_VERSION,
            "strategy": self.strategy,
            "original_query": self.original_query,
            "queries": list(self.queries),
            "atomic_queries": list(self.atomic_queries),
            "topics": list(self.topics),
            "query_count": len(self.queries),
            "supporting_query_count": len(self.supporting_queries),
            "supporting_topics": list(self.supporting_topics),
            "supporting_strategy": self.supporting_strategy,
            "fact_slot_queries": list(self.fact_slot_queries),
            "fact_slot_query_count": len(self.fact_slot_queries),
            "fact_slot_types": list(self.fact_slot_types),
            "fact_slot_strategy": self.fact_slot_strategy,
            "coverage_need_queries": list(self.coverage_need_queries),
            "coverage_need_count": len(self.coverage_need_queries),
            "coverage_need_types": list(self.coverage_need_types),
            "coverage_strategy": self.coverage_strategy,
            "coverage_selection_policy": (
                self.coverage_selection_policy
            ),
            "sufficiency_need_queries": list(
                self.sufficiency_need_queries
            ),
            "sufficiency_need_count": len(
                self.sufficiency_need_queries
            ),
            "sufficiency_need_types": list(
                self.sufficiency_need_types
            ),
            "sufficiency_need_answer_shapes": list(
                self.sufficiency_need_answer_shapes
            ),
            "sufficiency_strategy": self.sufficiency_strategy,
            "sufficiency_relation_shape_supported": (
                self.sufficiency_relation_shape_supported
            ),
            "sufficiency_relation_shape_reason": (
                self.sufficiency_relation_shape_reason
            ),
        }
        if self.sufficiency_strategy:
            payload["sufficiency_need_subjects"] = list(
                self.sufficiency_need_subjects
            )
        if (
            self.coverage_selection_policy
            == PREDICATE_AWARE_MARGINAL_GAIN_POLICY
        ):
            payload.update(
                {
                    "coverage_need_subjects": list(
                        self.coverage_need_subjects
                    ),
                    "coverage_need_answer_shapes": list(
                        self.coverage_need_answer_shapes
                    ),
                    "coverage_relation_shape_supported": (
                        self.coverage_relation_shape_supported
                    ),
                    "coverage_relation_shape_reason": (
                        self.coverage_relation_shape_reason
                    ),
                }
            )
        return payload


@dataclass(frozen=True)
class PlannedEvidenceResult:
    block: EvidenceBlock
    rrf_score: float
    matched_queries: tuple[str, ...]
    coverage_need_indexes: tuple[int, ...] = ()
    coverage_need_types: tuple[str, ...] = ()
    selected_by_coverage: bool = False
    protected_baseline_anchor: bool = False
    promoted_by_coverage: bool = False
    candidate_sources: tuple[str, ...] = ("retrieval",)
    replacement_evidence_id: str | None = None
    replacement_position: int | None = None
    promotion_sequence: int | None = None
    net_coverage_gain: int = 0
    coverage_before_count: int | None = None
    coverage_after_count: int | None = None
    coverage_before_need_indexes: tuple[int, ...] = ()
    coverage_after_need_indexes: tuple[int, ...] = ()
    added_need_indexes: tuple[int, ...] = ()
    victim_exclusive_need_indexes: tuple[int, ...] = ()
    query_diversity_preserved: bool | None = None


@dataclass(frozen=True)
class CoverageNeedMatch:
    need_index: int
    need_type: str
    score: float
    has_answer_signal: bool


@dataclass(frozen=True)
class _PredicateAwareNeed:
    query: str
    subject: str
    relation_type: str
    answer_shape: str


@dataclass(frozen=True)
class _PredicateCoverageAssessment:
    score: float
    confidence: float
    subject_alignment: float
    has_answer_signal: bool


_TOPIC_RULES = (
    _TopicRule(
        "qualification",
        "项目投标人资质、业绩和关键人员要求",
        re.compile(
            r"资质|资格|业绩|项目经理|建造师|关键人员|人员要求|信用要求"
        ),
    ),
    _TopicRule(
        "deadline",
        "项目投标截止时间、开标时间和文件递交要求",
        re.compile(r"投标截止|开标时间|递交截止|提交截止|截止日期"),
    ),
    _TopicRule(
        "payment",
        "项目付款、结算、审计和回款条件",
        re.compile(r"付款|支付|预付款|进度款|结算|审计付款|回款"),
    ),
    _TopicRule(
        "schedule",
        "项目工期、进度节点和延期违约责任",
        re.compile(r"工期|进度节点|竣工时间|延期|延误|逾期"),
    ),
    _TopicRule(
        "bond",
        "项目投标保证金、履约保证金和担保要求",
        re.compile(r"保证金|保函|担保"),
    ),
    _TopicRule(
        "evaluation",
        "项目评标办法、评分标准和否决投标条件",
        re.compile(r"评标|评分|定标|废标|否决投标"),
    ),
    _TopicRule(
        "pricing",
        "项目报价范围、控制价和计价方式",
        re.compile(
            r"控制价|最高限价|报价范围|计价|总价包干|清单计价|暂列金"
        ),
    ),
    _TopicRule(
        "contract_risk",
        "项目合同条款、违约责任、签证和索赔风险",
        re.compile(r"合同条款|违约责任|索赔|签证|争议|罚款"),
    ),
    _TopicRule(
        "technical_documents",
        "项目工程量清单、图纸和技术资料要求",
        re.compile(r"工程量清单|清单|图纸|技术规范|技术要求|材料表"),
    ),
    _TopicRule(
        "scope",
        "项目招标范围、承包内容和专业边界",
        re.compile(r"招标范围|承包范围|施工范围|工作范围|专业分包|分包"),
    ),
    _TopicRule(
        "site",
        "项目地点、现场条件和踏勘要求",
        re.compile(r"项目地点|建设地点|施工地点|现场条件|踏勘"),
    ),
    _TopicRule(
        "quality_warranty",
        "项目质量标准、验收和保修要求",
        re.compile(r"质量标准|质量要求|验收|保修|质保"),
    ),
)

_SEMANTIC_FACT_COMPANION_PATTERN = re.compile(
    r"风险|隐患|不利|后果|影响|损失"
)
_COMPOUND_FACT_PATTERN = re.compile(
    r"分别|[、，,；;]|以及|并且|同时|和|与"
)
_FACT_SLOT_SPLIT_PATTERN = re.compile(
    r"[，,；;、]|以及|并且|同时|和|与"
)
_FACT_SLOT_PREFIX_PATTERN = re.compile(
    r"^(?:请分别说明|请分别列明|分别说明|分别列明|请说明|请列明|请问|"
    r"还需要|需要)+"
)
_COVERAGE_SUBJECT_MARKERS = {
    "amount": re.compile(r"金额|价格|费用|造价"),
    "requirement": re.compile(r"要求"),
    "inclusion": re.compile(r"承包范围|范围|包含|包括"),
    "exclusion": re.compile(r"不包含|不包括|不含|除外"),
    "location": re.compile(r"地点|地址|哪里"),
    "quantity": re.compile(r"份数|套数|数量"),
    "time": re.compile(r"时间|日期|期限|要求"),
    "condition": re.compile(r"条件|方式|要求"),
    "standard": re.compile(r"标准|要求"),
}
_COVERAGE_ANSWER_SIGNALS = {
    "amount": re.compile(
        r"(?:RMB|人民币|￥|¥)\s*\d[\d,.]*|"
        r"\d[\d,.]*\s*(?:元|万元|%|％)",
        re.IGNORECASE,
    ),
    "requirement": re.compile(r"要求|应当|应按|必须|须|需|资格"),
    "inclusion": re.compile(r"包括|包含"),
    "exclusion": re.compile(r"不含|不包括|不包含|除外"),
    "location": re.compile(r"地点|地址|送达至|递交至"),
    "quantity": re.compile(r"\d+\s*(?:份|套|册|正本|副本)"),
    "time": re.compile(
        r"\d+\s*(?:年|月|日|时|分|天|日历天)|"
        r"\d{1,2}[:：]\d{2}"
    ),
    "condition": re.compile(r"付款方式|支付|结算|预付款|进度款"),
    "standard": re.compile(r"质量|验收|标准|规范|合格"),
}
_PREDICATE_ANSWER_SIGNALS = {
    **_COVERAGE_ANSWER_SIGNALS,
    "time": re.compile(
        r"\d+\s*(?:个?\s*工作日|自然日|日历日|日历天|"
        r"年|月|日|天|周|星期|小时|分钟|时|分)|"
        r"\d{1,2}[:：]\d{2}"
    ),
    "location": re.compile(
        r"(?:地点|地址)[：:]\s*\S{2,}|"
        r"(?:送达|递交)至\S{2,}|"
        r"\S{2,}(?:省|市|区|县|镇|街道|路|号|项目部)"
    ),
    "requirement": re.compile(
        r"应当|应按|必须|须|需要|不得|不低于|不少于|"
        r"(?:资质|资格)(?:要求)?[：:]?\s*\S{2,}|"
        r"(?:专业承包|总承包)\S*(?:级|等级)|"
        r"具备|提供\S{2,}"
    ),
    "condition": re.compile(
        r"(?:付款|支付|结算)(?:比例|节点|条件|方式)?"
        r"[：:]\s*\S{2,}|"
        r"(?:预付款|进度款|结算款|质保金)\S*"
        r"(?:\d+\s*[%％]|支付|扣留|返还)|"
        r"无预付款|\d+\s*[%％]"
    ),
    "standard": re.compile(
        r"达到|符合|执行|合格|不低于|优于|"
        r"按\S*(?:标准|规范)|验收\S*合格"
    ),
    "deliverable": re.compile(
        r"文件|图纸|图册|成果|清单|说明|材料表|"
        r"电子版|PDF|DWG|CAD|份|套|册",
        re.IGNORECASE,
    ),
    "responsibility": re.compile(
        r"负责|承担|配合|安装|施工|供货|预留|接线"
    ),
    "missing": re.compile(
        r"未提供|未包含|未附|缺少|缺失|不含|没有|"
        r"补取|补充|另行提供"
    ),
    "conflict": re.compile(
        r"冲突|不一致|未同步|未回写|重复|漏报|更正|变更|"
        r"最新版|书面确认"
    ),
    "entity_fact": re.compile(
        r"为|是|包括|包含|负责|完成|提交|达到|采用|"
        r"需要|要求|提供|约定|共|不少于|不得|未|无|"
        r"\d"
    ),
}
_PREDICATE_QUANTITY_SIGNAL = re.compile(
    r"\d+\s*(?:份|套|册|项|个|间|张|台|本)"
)
_PREDICATE_FORMAT_SIGNAL = re.compile(
    r"电子版|电子文件|PDF|DWG|CAD|XLSX?|DOCX?|格式",
    re.IGNORECASE,
)
_PREDICATE_SHARED_PATTERN = re.compile(
    r"^(?P<subjects>.+?)分别(?P<predicate>.+)$"
)
_PREDICATE_LEADING_SEPARATE_PATTERN = re.compile(
    r"^(?:请)?分别(?:说明|列明|写明|指出)?"
)
_PREDICATE_SUBJECT_SPLIT_PATTERN = re.compile(
    r"[、，,；;]|以及|并且|同时|和|与"
)
_PREDICATE_DIMENSION_SPLIT_PATTERN = re.compile(r"[，,；;]")
_PREDICATE_GENERIC_QUESTION_PATTERN = re.compile(
    r"^(?:分别)?(?:是什么|有哪些|如何|是多少|怎么规定|怎样规定)$"
)
_PREDICATE_SHARED_CONTEXT_PATTERN = re.compile(
    r"^(?P<subject>.+?)的"
    r"(?P<context>时间节点|时间要求|承包范围|施工范围|"
    r"提交要求|文件要求|成果要求|参数要求|质量标准|"
    r"验收标准|份数|套数|电子格式|职责边界)$"
)
_PREDICATE_META_SUBJECT_PATTERN = re.compile(
    r"^(?:本|该|此|当前|相关|上述|前述)*"
    r"(?:招标文件|施工合同|合同文件|工程量清单|报价清单|"
    r"招标清单|清单文件|项目资料|招标资料|合同资料)"
    r"(?:[“\"《][^”\"》]+[”\"》]|编制说明)?(?:中|内|里)?$"
)
_PREDICATE_PARTY_TOKEN = (
    r"(?:[\u4e00-\u9fffA-Za-z0-9]{1,12}单位|"
    r"[\u4e00-\u9fffA-Za-z0-9]{1,12}部门|"
    r"甲方|乙方|发包人|承包人|招标人|投标人)"
)
_PREDICATE_SUBJECT_MARKERS = {
    **_COVERAGE_SUBJECT_MARKERS,
    "deliverable": re.compile(
        r"需要完成|完成|成果|文件|图纸|图册|清单|说明|"
        r"提交|电子格式|电子版|格式|份数|套数"
    ),
    "responsibility": re.compile(
        r"负责|承担|配合|安装|施工|供货|预留|职责|工作"
    ),
    "missing": re.compile(
        r"参数|附件|是否提供|提供|缺少|缺失|补取|补充"
    ),
    "conflict": re.compile(
        r"冲突|一致|同步|回写|重复|漏报|更正|变更|风险"
    ),
    "entity_fact": re.compile(
        r"是什么|有哪些|如何|多少|情况|内容"
    ),
}
_MIN_COVERAGE_SCORE = 0.8
_DIRECT_ALIGNMENT_NON_ENTITY_PATTERN = re.compile(
    r"^(?:本|该|此|当前|相关|上述|前述)*"
    r"(?:招标文件|施工合同|合同文件|工程量清单|报价清单|"
    r"招标清单|清单文件|项目资料|招标资料|合同资料)"
    r"(?:中|内|里|是否|已经|已|的)*$"
)
_DIRECT_ALIGNMENT_RELATION_ONLY_PATTERN = re.compile(
    r"^(?:是否|已经|已|完整|分别|各自|具体|需要|还需)*"
    r"(?:提交|递交|退还|返还|支付|缴纳|约定|包含|包括|"
    r"要求|提供|说明|确认)"
    r"(?:方式|形式|节点|条件|时间|地点|金额|要求|情况|内容)?$"
)
_DIRECT_ALIGNMENT_META_PHRASES = {
    "是否已经",
    "是否完整",
    "已经完整",
    "完整约定",
    "分别如何",
    "是否一致",
    "还需要向",
}


def plan_tender_query(
    query: str,
    *,
    max_query_count: int = DEFAULT_MAX_QUERY_COUNT,
    enable_semantic_fact_companion: bool = False,
    enable_atomic_fact_slots: bool = False,
    enable_candidate_coverage_selection: bool = False,
    enable_evidence_sufficiency_assessment: bool = False,
    candidate_coverage_selection_policy: str = (
        DEFAULT_COVERAGE_SELECTION_POLICY
    ),
) -> TenderQueryPlan:
    """Create a deterministic, bounded query plan without another LLM call."""

    original = _normalize_query(query)
    if not original:
        raise ValueError("query must not be empty")
    normalized_coverage_policy = str(
        candidate_coverage_selection_policy
        or DEFAULT_COVERAGE_SELECTION_POLICY
    ).strip()
    if normalized_coverage_policy not in _COVERAGE_SELECTION_POLICIES:
        raise ValueError(
            "unsupported candidate coverage selection policy: "
            f"{normalized_coverage_policy}"
        )
    bounded_count = max(1, min(int(max_query_count), 10))
    matched_rules = [
        rule for rule in _TOPIC_RULES if rule.pattern.search(original)
    ]
    clauses = _split_clauses(original)

    queries = [original]
    topics: list[str] = []
    supporting_queries: list[str] = []
    supporting_topics: list[str] = []
    supporting_strategy: str | None = None
    fact_slot_queries: list[str] = []
    fact_slot_types: list[str] = []
    fact_slot_strategy: str | None = None
    coverage_need_queries: list[str] = []
    coverage_need_types: list[str] = []
    coverage_need_subjects: list[str] = []
    coverage_need_answer_shapes: list[str] = []
    coverage_strategy: str | None = None
    coverage_relation_shape_supported: bool | None = None
    coverage_relation_shape_reason: str | None = None
    sufficiency_need_queries: list[str] = []
    sufficiency_need_types: list[str] = []
    sufficiency_need_subjects: list[str] = []
    sufficiency_need_answer_shapes: list[str] = []
    sufficiency_strategy: str | None = None
    sufficiency_relation_shape_supported: bool | None = None
    sufficiency_relation_shape_reason: str | None = None
    predicate_needs: list[_PredicateAwareNeed] = []
    strategy = "single_query"
    if len(matched_rules) >= 2:
        strategy = "topic_decomposition"
        for rule in matched_rules:
            topics.append(rule.topic)
            _append_unique(queries, rule.query, limit=bounded_count)
    elif len(clauses) >= 2:
        strategy = "clause_decomposition"
        for clause in clauses:
            _append_unique(queries, clause, limit=bounded_count)
    if (
        enable_semantic_fact_companion
        and len(matched_rules) == 1
        and _SEMANTIC_FACT_COMPANION_PATTERN.search(original)
    ):
        rule = matched_rules[0]
        _append_unique(
            supporting_queries,
            rule.query,
            limit=1,
        )
        if supporting_queries:
            supporting_topics.append(rule.topic)
            supporting_strategy = "semantic_fact_companion"
    if enable_atomic_fact_slots:
        fact_slots = _extract_atomic_fact_slots(
            original,
            max_slot_count=DEFAULT_MAX_FACT_SLOT_COUNT,
        )
        existing_fingerprints = {
            _fingerprint(item)
            for item in (*queries, *supporting_queries)
        }
        for slot_query, slot_type in fact_slots:
            fingerprint = _fingerprint(slot_query)
            if fingerprint in existing_fingerprints:
                continue
            fact_slot_queries.append(slot_query)
            fact_slot_types.append(slot_type)
            existing_fingerprints.add(fingerprint)
        if fact_slot_queries:
            fact_slot_strategy = "compound_surface_fact_slots"
    if enable_candidate_coverage_selection:
        if (
            normalized_coverage_policy
            == PREDICATE_AWARE_MARGINAL_GAIN_POLICY
        ):
            predicate_needs = _extract_predicate_aware_fact_needs(
                original,
                max_slot_count=_MAX_PREDICATE_AWARE_NEED_COUNT,
            )
            coverage_need_queries = [
                need.query for need in predicate_needs
            ]
            coverage_need_types = [
                need.relation_type for need in predicate_needs
            ]
            coverage_need_subjects = [
                need.subject for need in predicate_needs
            ]
            coverage_need_answer_shapes = [
                need.answer_shape for need in predicate_needs
            ]
            coverage_relation_shape_supported = bool(
                coverage_need_queries
            )
            coverage_relation_shape_reason = (
                None
                if coverage_need_queries
                else "unsupported_relation_shape"
            )
            coverage_strategy = "predicate_aware_marginal_gain"
        else:
            coverage_needs = _extract_atomic_fact_slots(
                original,
                max_slot_count=DEFAULT_MAX_FACT_SLOT_COUNT,
            )
            coverage_need_queries = [
                slot_query for slot_query, _ in coverage_needs
            ]
            coverage_need_types = [
                slot_type for _, slot_type in coverage_needs
            ]
            if coverage_need_queries:
                coverage_strategy = (
                    "anchor_preserving_answer_signal_need_coverage"
                    if normalized_coverage_policy
                    == ANCHOR_PRESERVING_COVERAGE_SELECTION_POLICY
                    else "answer_signal_need_coverage"
                )
    if enable_evidence_sufficiency_assessment:
        if not predicate_needs:
            predicate_needs = _extract_predicate_aware_fact_needs(
                original,
                max_slot_count=_MAX_PREDICATE_AWARE_NEED_COUNT,
            )
        sufficiency_need_queries = [
            need.query for need in predicate_needs
        ]
        sufficiency_need_types = [
            need.relation_type for need in predicate_needs
        ]
        sufficiency_need_subjects = [
            need.subject for need in predicate_needs
        ]
        sufficiency_need_answer_shapes = [
            need.answer_shape for need in predicate_needs
        ]
        sufficiency_relation_shape_supported = bool(
            sufficiency_need_queries
        )
        sufficiency_relation_shape_reason = (
            None
            if sufficiency_need_queries
            else "unsupported_relation_shape"
        )
        sufficiency_strategy = (
            PREDICATE_AWARE_SUFFICIENCY_ASSESSMENT
        )

    return TenderQueryPlan(
        original_query=original,
        queries=tuple(queries[:bounded_count]),
        topics=tuple(topics[: max(0, bounded_count - 1)]),
        strategy=strategy,
        supporting_queries=tuple(supporting_queries),
        supporting_topics=tuple(supporting_topics),
        supporting_strategy=supporting_strategy,
        fact_slot_queries=tuple(fact_slot_queries),
        fact_slot_types=tuple(fact_slot_types),
        fact_slot_strategy=fact_slot_strategy,
        coverage_need_queries=tuple(coverage_need_queries),
        coverage_need_types=tuple(coverage_need_types),
        coverage_need_subjects=tuple(coverage_need_subjects),
        coverage_need_answer_shapes=tuple(
            coverage_need_answer_shapes
        ),
        coverage_strategy=coverage_strategy,
        coverage_selection_policy=(
            normalized_coverage_policy
            if (
                coverage_need_queries
                or normalized_coverage_policy
                == PREDICATE_AWARE_MARGINAL_GAIN_POLICY
            )
            else "off"
        ),
        coverage_relation_shape_supported=(
            coverage_relation_shape_supported
        ),
        coverage_relation_shape_reason=(
            coverage_relation_shape_reason
        ),
        sufficiency_need_queries=tuple(sufficiency_need_queries),
        sufficiency_need_types=tuple(sufficiency_need_types),
        sufficiency_need_subjects=tuple(
            sufficiency_need_subjects
        ),
        sufficiency_need_answer_shapes=tuple(
            sufficiency_need_answer_shapes
        ),
        sufficiency_strategy=sufficiency_strategy,
        sufficiency_relation_shape_supported=(
            sufficiency_relation_shape_supported
        ),
        sufficiency_relation_shape_reason=(
            sufficiency_relation_shape_reason
        ),
    )


def merge_planned_results(
    *,
    plan: TenderQueryPlan,
    ranked_results: Sequence[tuple[str, Sequence[EvidenceBlock]]],
    top_k: int,
    supplemental_blocks: Sequence[EvidenceBlock] = (),
) -> list[PlannedEvidenceResult]:
    """Fuse rankings and bounded structural candidates into the final Top K."""

    bounded_top_k = max(1, min(int(top_k), 20))
    by_evidence_id: dict[str, dict[str, object]] = {}
    for query_index, (planned_query, blocks) in enumerate(ranked_results):
        for rank, block in enumerate(blocks, start=1):
            entry = by_evidence_id.setdefault(
                block.evidence_id,
                {
                    "block": block,
                    "score": 0.0,
                    "matched_queries": [],
                    "first_seen": (query_index, rank),
                    "candidate_sources": ["retrieval"],
                },
            )
            entry["score"] = float(entry["score"]) + (
                1.0 / (QUERY_RRF_K + rank)
            )
            matched_queries = entry["matched_queries"]
            if (
                isinstance(matched_queries, list)
                and planned_query not in matched_queries
            ):
                matched_queries.append(planned_query)
    supplemental_query_index = len(ranked_results)
    for supplemental_index, block in enumerate(
        supplemental_blocks,
        start=1,
    ):
        entry = by_evidence_id.setdefault(
            block.evidence_id,
            {
                "block": block,
                "score": 0.0,
                "matched_queries": [],
                "first_seen": (
                    supplemental_query_index,
                    supplemental_index,
                ),
                "candidate_sources": [],
            },
        )
        candidate_sources = entry["candidate_sources"]
        if (
            isinstance(candidate_sources, list)
            and "adjacent_context" not in candidate_sources
        ):
            candidate_sources.append("adjacent_context")

    ranked_ids = sorted(
        by_evidence_id,
        key=lambda evidence_id: (
            -float(by_evidence_id[evidence_id]["score"]),
            by_evidence_id[evidence_id]["first_seen"],
            evidence_id,
        ),
    )
    baseline_selected_ids = _select_baseline_top_k(
        ranked_results=ranked_results,
        ranked_ids=ranked_ids,
        top_k=bounded_top_k,
    )
    selected_ids: list[str]
    coverage_by_evidence: dict[str, set[int]] = {}
    protected_baseline_ids: set[str] = set()
    promoted_by_coverage_ids: set[str] = set()
    promotion_audit_by_evidence: dict[
        str,
        dict[str, object],
    ] = {}
    if plan.coverage_need_queries:
        if (
            plan.coverage_selection_policy
            == ANCHOR_PRESERVING_COVERAGE_SELECTION_POLICY
        ):
            (
                selected_ids,
                coverage_by_evidence,
                protected_baseline_ids,
                promoted_by_coverage_ids,
            ) = _select_anchor_preserving_coverage_candidates(
                plan=plan,
                by_evidence_id=by_evidence_id,
                ranked_ids=ranked_ids,
                baseline_selected_ids=baseline_selected_ids,
                top_k=bounded_top_k,
            )
        elif (
            plan.coverage_selection_policy
            == PREDICATE_AWARE_MARGINAL_GAIN_POLICY
        ):
            (
                selected_ids,
                coverage_by_evidence,
                protected_baseline_ids,
                promoted_by_coverage_ids,
                promotion_audit_by_evidence,
            ) = _select_predicate_aware_marginal_gain_candidates(
                plan=plan,
                by_evidence_id=by_evidence_id,
                ranked_results=ranked_results,
                ranked_ids=ranked_ids,
                baseline_selected_ids=baseline_selected_ids,
                top_k=bounded_top_k,
            )
        else:
            selected_ids, coverage_by_evidence = (
                _select_need_coverage_candidates(
                    plan=plan,
                    by_evidence_id=by_evidence_id,
                    ranked_ids=ranked_ids,
                    top_k=bounded_top_k,
                )
            )
            promoted_by_coverage_ids = set(selected_ids).difference(
                ranked_ids[:bounded_top_k]
            )
            selected_ids = _extend_selection_with_diversity_and_rrf(
                selected_ids=selected_ids,
                ranked_results=ranked_results,
                ranked_ids=ranked_ids,
                top_k=bounded_top_k,
            )
    else:
        selected_ids = baseline_selected_ids

    results: list[PlannedEvidenceResult] = []
    for evidence_id in selected_ids[:bounded_top_k]:
        entry = by_evidence_id[evidence_id]
        matched_queries = entry["matched_queries"]
        promotion_audit = promotion_audit_by_evidence.get(
            evidence_id,
            {},
        )
        results.append(
            PlannedEvidenceResult(
                block=entry["block"],
                rrf_score=round(float(entry["score"]), 8),
                matched_queries=tuple(
                    matched_queries
                    if isinstance(matched_queries, list)
                    else []
                ),
                coverage_need_indexes=tuple(
                    sorted(coverage_by_evidence.get(evidence_id, set()))
                ),
                coverage_need_types=tuple(
                    plan.coverage_need_types[index]
                    for index in sorted(
                        coverage_by_evidence.get(evidence_id, set())
                    )
                ),
                selected_by_coverage=(
                    evidence_id in coverage_by_evidence
                ),
                protected_baseline_anchor=(
                    evidence_id in protected_baseline_ids
                ),
                promoted_by_coverage=(
                    evidence_id in promoted_by_coverage_ids
                ),
                candidate_sources=tuple(
                    entry["candidate_sources"]
                    if isinstance(entry["candidate_sources"], list)
                    else []
                ),
                replacement_evidence_id=(
                    str(
                        promotion_audit.get(
                            "replacement_evidence_id"
                        )
                    )
                    if promotion_audit.get(
                        "replacement_evidence_id"
                    )
                    else None
                ),
                replacement_position=(
                    int(promotion_audit["replacement_position"])
                    if promotion_audit.get(
                        "replacement_position"
                    )
                    is not None
                    else None
                ),
                promotion_sequence=(
                    int(promotion_audit["promotion_sequence"])
                    if promotion_audit.get("promotion_sequence")
                    is not None
                    else None
                ),
                net_coverage_gain=int(
                    promotion_audit.get(
                        "net_coverage_gain",
                        0,
                    )
                ),
                coverage_before_count=(
                    int(promotion_audit["coverage_before_count"])
                    if promotion_audit.get(
                        "coverage_before_count"
                    )
                    is not None
                    else None
                ),
                coverage_after_count=(
                    int(promotion_audit["coverage_after_count"])
                    if promotion_audit.get(
                        "coverage_after_count"
                    )
                    is not None
                    else None
                ),
                coverage_before_need_indexes=tuple(
                    int(index)
                    for index in promotion_audit.get(
                        "coverage_before_need_indexes",
                        (),
                    )
                ),
                coverage_after_need_indexes=tuple(
                    int(index)
                    for index in promotion_audit.get(
                        "coverage_after_need_indexes",
                        (),
                    )
                ),
                added_need_indexes=tuple(
                    int(index)
                    for index in promotion_audit.get(
                        "added_need_indexes",
                        (),
                    )
                ),
                victim_exclusive_need_indexes=tuple(
                    int(index)
                    for index in promotion_audit.get(
                        "victim_exclusive_need_indexes",
                        (),
                    )
                ),
                query_diversity_preserved=(
                    bool(
                        promotion_audit.get(
                            "query_diversity_preserved"
                        )
                    )
                    if promotion_audit.get(
                        "query_diversity_preserved"
                    )
                    is not None
                    else None
                ),
            )
        )
    return results


def _select_baseline_top_k(
    *,
    ranked_results: Sequence[tuple[str, Sequence[EvidenceBlock]]],
    ranked_ids: Sequence[str],
    top_k: int,
) -> list[str]:
    """Preserve auxiliary-query diversity, then fill by global RRF order."""

    return _extend_selection_with_diversity_and_rrf(
        selected_ids=(),
        ranked_results=ranked_results,
        ranked_ids=ranked_ids,
        top_k=top_k,
    )


def _extend_selection_with_diversity_and_rrf(
    *,
    selected_ids: Sequence[str],
    ranked_results: Sequence[tuple[str, Sequence[EvidenceBlock]]],
    ranked_ids: Sequence[str],
    top_k: int,
) -> list[str]:
    """Apply the existing auxiliary-query diversity and RRF fill order."""

    filled_ids = list(selected_ids)
    if len(ranked_results) > 1 and top_k > 1:
        for _, blocks in ranked_results[1:]:
            candidate = next(
                (
                    block.evidence_id
                    for block in blocks
                    if block.evidence_id not in filled_ids
                ),
                None,
            )
            if candidate is not None:
                filled_ids.append(candidate)
            if len(filled_ids) >= top_k:
                return filled_ids
    for evidence_id in ranked_ids:
        if evidence_id not in filled_ids:
            filled_ids.append(evidence_id)
        if len(filled_ids) >= top_k:
            break
    return filled_ids


def _select_predicate_aware_marginal_gain_candidates(
    *,
    plan: TenderQueryPlan,
    by_evidence_id: dict[str, dict[str, object]],
    ranked_results: Sequence[
        tuple[str, Sequence[EvidenceBlock]]
    ],
    ranked_ids: Sequence[str],
    baseline_selected_ids: Sequence[str],
    top_k: int,
) -> tuple[
    list[str],
    dict[str, set[int]],
    set[str],
    set[str],
    dict[str, dict[str, object]],
]:
    """Promote only when simulated relation coverage strictly grows."""

    selected_ids = list(baseline_selected_ids[:top_k])
    if not selected_ids:
        return [], {}, set(), set(), {}

    coverage_by_candidate: dict[str, set[int]] = {}
    assessments_by_candidate: dict[
        str,
        dict[int, _PredicateCoverageAssessment],
    ] = {}
    need_count = min(
        len(plan.coverage_need_queries),
        len(plan.coverage_need_types),
        len(plan.coverage_need_subjects),
        len(plan.coverage_need_answer_shapes),
    )
    for evidence_id in ranked_ids:
        entry = by_evidence_id[evidence_id]
        block = entry["block"]
        if not isinstance(block, EvidenceBlock):
            continue
        for need_index in range(need_count):
            assessment = _predicate_coverage_assessment(
                need_query=plan.coverage_need_queries[need_index],
                need_type=plan.coverage_need_types[need_index],
                need_subject=plan.coverage_need_subjects[need_index],
                answer_shape=(
                    plan.coverage_need_answer_shapes[need_index]
                ),
                content=block.coverage_content,
                rrf_score=float(entry["score"]),
            )
            if (
                assessment.subject_alignment
                < _MIN_PREDICATE_SUBJECT_ALIGNMENT
                or not assessment.has_answer_signal
                or assessment.score
                < _MIN_PREDICATE_COVERAGE_SCORE
            ):
                continue
            coverage_by_candidate.setdefault(
                evidence_id,
                set(),
            ).add(need_index)
            assessments_by_candidate.setdefault(
                evidence_id,
                {},
            )[need_index] = assessment

    auxiliary_queries = tuple(
        query for query, _ in ranked_results[1:]
    )
    protected_ids = {selected_ids[0]}
    protected_ids.update(
        _unique_auxiliary_query_representatives(
            selected_ids=selected_ids,
            by_evidence_id=by_evidence_id,
            auxiliary_queries=auxiliary_queries,
        )
    )
    promoted_ids: set[str] = set()
    audit_by_evidence: dict[str, dict[str, object]] = {}

    for _ in range(_MAX_MARGINAL_PROMOTIONS_PER_CASE):
        coverage_before = _coverage_union(
            selected_ids,
            coverage_by_candidate,
        )
        opportunities: list[
            tuple[
                tuple[object, ...],
                str,
                int,
                str,
                set[int],
                set[int],
            ]
        ] = []
        for candidate_id in ranked_ids:
            if candidate_id in selected_ids:
                continue
            candidate_coverage = coverage_by_candidate.get(
                candidate_id,
                set(),
            )
            if not candidate_coverage:
                continue
            for replacement_index in range(
                1,
                len(selected_ids),
            ):
                victim_id = selected_ids[replacement_index]
                if (
                    victim_id in protected_ids
                    or victim_id in promoted_ids
                ):
                    continue
                remaining_ids = [
                    evidence_id
                    for index, evidence_id in enumerate(selected_ids)
                    if index != replacement_index
                ]
                remaining_coverage = _coverage_union(
                    remaining_ids,
                    coverage_by_candidate,
                )
                victim_exclusive = coverage_by_candidate.get(
                    victim_id,
                    set(),
                ).difference(remaining_coverage)
                if not victim_exclusive.issubset(
                    candidate_coverage
                ):
                    continue
                simulated_ids = list(selected_ids)
                simulated_ids[replacement_index] = candidate_id
                if not _query_diversity_is_preserved(
                    before_ids=selected_ids,
                    after_ids=simulated_ids,
                    by_evidence_id=by_evidence_id,
                    auxiliary_queries=auxiliary_queries,
                ):
                    continue
                coverage_after = _coverage_union(
                    simulated_ids,
                    coverage_by_candidate,
                )
                net_gain = (
                    len(coverage_after) - len(coverage_before)
                )
                if net_gain < 1:
                    continue
                new_need_indexes = coverage_after.difference(
                    coverage_before
                )
                candidate_assessments = (
                    assessments_by_candidate.get(candidate_id, {})
                )
                confidence = sum(
                    candidate_assessments[index].confidence
                    for index in new_need_indexes
                    if index in candidate_assessments
                )
                coverage_score = sum(
                    candidate_assessments[index].score
                    for index in new_need_indexes
                    if index in candidate_assessments
                )
                candidate_rrf = float(
                    by_evidence_id[candidate_id]["score"]
                )
                victim_rrf = float(
                    by_evidence_id[victim_id]["score"]
                )
                sort_key: tuple[object, ...] = (
                    -net_gain,
                    -confidence,
                    -coverage_score,
                    -candidate_rrf,
                    candidate_id,
                    len(victim_exclusive),
                    victim_rrf,
                    -replacement_index,
                    victim_id,
                )
                opportunities.append(
                    (
                        sort_key,
                        candidate_id,
                        replacement_index,
                        victim_id,
                        coverage_after,
                        victim_exclusive,
                    )
                )
        if not opportunities:
            break
        opportunities.sort(key=lambda item: item[0])
        (
            _,
            promoted_id,
            replacement_index,
            victim_id,
            coverage_after,
            victim_exclusive,
        ) = opportunities[0]
        selected_ids[replacement_index] = promoted_id
        promotion_sequence = len(promoted_ids) + 1
        promoted_ids.add(promoted_id)
        audit_by_evidence[promoted_id] = {
            "replacement_evidence_id": victim_id,
            "replacement_position": replacement_index + 1,
            "promotion_sequence": promotion_sequence,
            "net_coverage_gain": (
                len(coverage_after) - len(coverage_before)
            ),
            "coverage_before_count": len(coverage_before),
            "coverage_after_count": len(coverage_after),
            "coverage_before_need_indexes": tuple(
                sorted(coverage_before)
            ),
            "coverage_after_need_indexes": tuple(
                sorted(coverage_after)
            ),
            "added_need_indexes": tuple(
                sorted(coverage_after.difference(coverage_before))
            ),
            "victim_exclusive_need_indexes": tuple(
                sorted(victim_exclusive)
            ),
            "query_diversity_preserved": True,
        }

    final_coverage = {
        evidence_id: set(
            coverage_by_candidate.get(evidence_id, set())
        )
        for evidence_id in selected_ids
        if coverage_by_candidate.get(evidence_id)
    }
    return (
        selected_ids,
        final_coverage,
        protected_ids.intersection(selected_ids),
        promoted_ids.intersection(selected_ids),
        audit_by_evidence,
    )


def _coverage_union(
    evidence_ids: Sequence[str],
    coverage_by_candidate: dict[str, set[int]],
) -> set[int]:
    return {
        need_index
        for evidence_id in evidence_ids
        for need_index in coverage_by_candidate.get(
            evidence_id,
            set(),
        )
    }


def _unique_auxiliary_query_representatives(
    *,
    selected_ids: Sequence[str],
    by_evidence_id: dict[str, dict[str, object]],
    auxiliary_queries: Sequence[str],
) -> set[str]:
    protected_ids: set[str] = set()
    for query in auxiliary_queries:
        representatives = [
            evidence_id
            for evidence_id in selected_ids
            if query
            in (
                by_evidence_id[evidence_id]["matched_queries"]
                if isinstance(
                    by_evidence_id[evidence_id]["matched_queries"],
                    list,
                )
                else []
            )
        ]
        if len(representatives) == 1:
            protected_ids.add(representatives[0])
    return protected_ids


def _query_diversity_is_preserved(
    *,
    before_ids: Sequence[str],
    after_ids: Sequence[str],
    by_evidence_id: dict[str, dict[str, object]],
    auxiliary_queries: Sequence[str],
) -> bool:
    for query in auxiliary_queries:
        represented_before = any(
            query
            in (
                by_evidence_id[evidence_id]["matched_queries"]
                if isinstance(
                    by_evidence_id[evidence_id]["matched_queries"],
                    list,
                )
                else []
            )
            for evidence_id in before_ids
        )
        represented_after = any(
            query
            in (
                by_evidence_id[evidence_id]["matched_queries"]
                if isinstance(
                    by_evidence_id[evidence_id]["matched_queries"],
                    list,
                )
                else []
            )
            for evidence_id in after_ids
        )
        if represented_before and not represented_after:
            return False
    return True


def _predicate_coverage_assessment(
    *,
    need_query: str,
    need_type: str,
    need_subject: str,
    answer_shape: str,
    content: str,
    rrf_score: float,
) -> _PredicateCoverageAssessment:
    normalized_subject = _coverage_normalize(need_subject)
    normalized_content = _coverage_normalize(content)
    subject_bigrams = _coverage_bigrams(normalized_subject)
    content_bigrams = _coverage_bigrams(normalized_content)
    exact_subject = bool(
        len(normalized_subject) >= 2
        and normalized_subject in normalized_content
    )
    subject_alignment = (
        1.0
        if exact_subject
        else (
            len(subject_bigrams.intersection(content_bigrams))
            / max(1, len(subject_bigrams))
        )
    )
    answer_pattern = _PREDICATE_ANSWER_SIGNALS.get(
        need_type,
        _PREDICATE_ANSWER_SIGNALS["entity_fact"],
    )
    has_answer_signal = bool(answer_pattern.search(content))
    if "quantity" in answer_shape:
        has_answer_signal = bool(
            has_answer_signal
            and _PREDICATE_QUANTITY_SIGNAL.search(content)
        )
    if "format" in answer_shape:
        has_answer_signal = bool(
            has_answer_signal
            and _PREDICATE_FORMAT_SIGNAL.search(content)
        )
    if "collaboration" in answer_shape:
        has_answer_signal = bool(
            has_answer_signal
            and re.search(r"配合|协助|协调|预留", content)
        )
    if need_type == "entity_fact" and exact_subject:
        remainder = normalized_content.replace(
            normalized_subject,
            "",
            1,
        )
        has_answer_signal = bool(
            has_answer_signal and len(remainder) >= 2
        )
    need_bigrams = _coverage_bigrams(
        _coverage_normalize(need_query)
    )
    relation_coverage = (
        len(need_bigrams.intersection(content_bigrams))
        / max(1, len(need_bigrams))
    )
    score = (
        3.0 * subject_alignment
        + 2.0 * float(has_answer_signal)
        + 1.5 * relation_coverage
        + 2.0 * max(0.0, float(rrf_score))
    )
    confidence = min(
        1.0,
        0.65 * subject_alignment
        + 0.25 * float(has_answer_signal)
        + 0.10 * relation_coverage,
    )
    return _PredicateCoverageAssessment(
        score=score,
        confidence=confidence,
        subject_alignment=subject_alignment,
        has_answer_signal=has_answer_signal,
    )


def _select_anchor_preserving_coverage_candidates(
    *,
    plan: TenderQueryPlan,
    by_evidence_id: dict[str, dict[str, object]],
    ranked_ids: Sequence[str],
    baseline_selected_ids: Sequence[str],
    top_k: int,
) -> tuple[
    list[str],
    dict[str, set[int]],
    set[str],
    set[str],
]:
    """Fill weak baseline positions without displacing direct anchors."""

    selected_ids = list(baseline_selected_ids[:top_k])
    if not selected_ids:
        return [], {}, set(), set()

    protected_ids = {selected_ids[0]}
    promoted_ids: set[str] = set()
    coverage_by_evidence: dict[str, set[int]] = {}
    for evidence_id in selected_ids:
        entry = by_evidence_id[evidence_id]
        block = entry["block"]
        if not isinstance(block, EvidenceBlock):
            continue
        if _direct_alignment_length(
            query=plan.original_query,
            content=block.coverage_content,
        ) >= _MIN_DIRECT_ALIGNMENT_CHARS:
            protected_ids.add(evidence_id)

    for need_index, (need_query, need_type) in enumerate(
        zip(plan.coverage_need_queries, plan.coverage_need_types)
    ):
        scored: list[tuple[float, float, int, str]] = []
        for evidence_id in ranked_ids:
            entry = by_evidence_id[evidence_id]
            block = entry["block"]
            if not isinstance(block, EvidenceBlock):
                continue
            score, has_answer_signal = _coverage_score(
                need_query=need_query,
                need_type=need_type,
                content=block.coverage_content,
                rrf_score=float(entry["score"]),
                adjacent_context=(
                    "adjacent_context"
                    in (
                        entry["candidate_sources"]
                        if isinstance(
                            entry["candidate_sources"],
                            list,
                        )
                        else []
                    )
                ),
            )
            direct_alignment_length = _direct_alignment_length(
                query=need_query,
                content=block.coverage_content,
            )
            if (
                not has_answer_signal
                or score < _MIN_COVERAGE_SCORE
                or direct_alignment_length
                < _MIN_DIRECT_ALIGNMENT_CHARS
            ):
                continue
            scored.append(
                (
                    score,
                    float(entry["score"]),
                    direct_alignment_length,
                    evidence_id,
                )
            )
        scored.sort(
            key=lambda item: (
                -item[2],
                -item[0],
                -item[1],
                item[3],
            )
        )
        covered_id = next(
            (
                evidence_id
                for _, _, _, evidence_id in scored
                if evidence_id in selected_ids
            ),
            None,
        )
        if covered_id is None:
            replacement_id = next(
                (
                    evidence_id
                    for _, _, _, evidence_id in scored
                    if evidence_id not in selected_ids
                ),
                None,
            )
            replacement_index = next(
                (
                    index
                    for index in range(
                        len(selected_ids) - 1,
                        -1,
                        -1,
                    )
                    if selected_ids[index] not in protected_ids
                    and selected_ids[index]
                    not in coverage_by_evidence
                ),
                None,
            )
            if (
                replacement_id is not None
                and replacement_index is not None
            ):
                selected_ids[replacement_index] = replacement_id
                promoted_ids.add(replacement_id)
                covered_id = replacement_id
        if covered_id is not None:
            coverage_by_evidence.setdefault(covered_id, set()).add(
                need_index
            )

    return (
        selected_ids,
        coverage_by_evidence,
        protected_ids.intersection(selected_ids),
        promoted_ids.intersection(selected_ids),
    )


def _select_need_coverage_candidates(
    *,
    plan: TenderQueryPlan,
    by_evidence_id: dict[str, dict[str, object]],
    ranked_ids: Sequence[str],
    top_k: int,
) -> tuple[list[str], dict[str, set[int]]]:
    selected_ids: list[str] = []
    coverage_by_evidence: dict[str, set[int]] = {}
    for need_index, (need_query, need_type) in enumerate(
        zip(plan.coverage_need_queries, plan.coverage_need_types)
    ):
        scored: list[tuple[bool, float, float, str]] = []
        for evidence_id in ranked_ids:
            entry = by_evidence_id[evidence_id]
            block = entry["block"]
            if not isinstance(block, EvidenceBlock):
                continue
            score, has_answer_signal = _coverage_score(
                need_query=need_query,
                need_type=need_type,
                content=block.coverage_content,
                rrf_score=float(entry["score"]),
                adjacent_context=(
                    "adjacent_context"
                    in (
                        entry["candidate_sources"]
                        if isinstance(
                            entry["candidate_sources"],
                            list,
                        )
                        else []
                    )
                ),
            )
            scored.append(
                (
                    has_answer_signal,
                    score,
                    float(entry["score"]),
                    evidence_id,
                )
            )
        scored.sort(
            key=lambda item: (
                not item[0],
                -item[1],
                -item[2],
                item[3],
            )
        )
        covered_id = next(
            (
                evidence_id
                for has_signal, score, _, evidence_id in scored
                if evidence_id in selected_ids
                and has_signal
                and score >= _MIN_COVERAGE_SCORE
            ),
            None,
        )
        if covered_id is None:
            covered_id = next(
                (
                    evidence_id
                    for has_signal, score, _, evidence_id in scored
                    if evidence_id not in selected_ids
                    and has_signal
                    and score >= _MIN_COVERAGE_SCORE
                ),
                None,
            )
            if covered_id is not None:
                selected_ids.append(covered_id)
        if covered_id is not None:
            coverage_by_evidence.setdefault(covered_id, set()).add(
                need_index
            )
        if len(selected_ids) >= top_k:
            break
    return selected_ids, coverage_by_evidence


def match_block_coverage_needs(
    *,
    plan: TenderQueryPlan,
    block: EvidenceBlock,
    adjacent_context: bool = False,
    require_answer_signal: bool = True,
) -> tuple[CoverageNeedMatch, ...]:
    matches: list[CoverageNeedMatch] = []
    for need_index, (need_query, need_type) in enumerate(
        zip(plan.coverage_need_queries, plan.coverage_need_types)
    ):
        score, has_answer_signal = _coverage_score(
            need_query=need_query,
            need_type=need_type,
            content=block.coverage_content,
            rrf_score=0.0,
            adjacent_context=adjacent_context,
        )
        if score < _MIN_COVERAGE_SCORE:
            continue
        if require_answer_signal and not has_answer_signal:
            continue
        matches.append(
            CoverageNeedMatch(
                need_index=need_index,
                need_type=need_type,
                score=round(score, 8),
                has_answer_signal=has_answer_signal,
            )
        )
    return tuple(matches)


def match_block_sufficiency_needs(
    *,
    plan: TenderQueryPlan,
    block: EvidenceBlock,
    rrf_score: float = 0.0,
) -> tuple[CoverageNeedMatch, ...]:
    """Match direct relation answers without changing result selection."""

    matches: list[CoverageNeedMatch] = []
    need_count = min(
        len(plan.sufficiency_need_queries),
        len(plan.sufficiency_need_types),
        len(plan.sufficiency_need_subjects),
        len(plan.sufficiency_need_answer_shapes),
    )
    for need_index in range(need_count):
        assessment = _predicate_coverage_assessment(
            need_query=plan.sufficiency_need_queries[need_index],
            need_type=plan.sufficiency_need_types[need_index],
            need_subject=plan.sufficiency_need_subjects[need_index],
            answer_shape=(
                plan.sufficiency_need_answer_shapes[need_index]
            ),
            content=block.coverage_content,
            rrf_score=rrf_score,
        )
        if (
            assessment.subject_alignment
            < _MIN_PREDICATE_SUBJECT_ALIGNMENT
            or not assessment.has_answer_signal
            or assessment.score
            < _MIN_PREDICATE_COVERAGE_SCORE
        ):
            continue
        matches.append(
            CoverageNeedMatch(
                need_index=need_index,
                need_type=plan.sufficiency_need_types[need_index],
                score=round(assessment.score, 8),
                has_answer_signal=assessment.has_answer_signal,
            )
        )
    return tuple(matches)


def _coverage_score(
    *,
    need_query: str,
    need_type: str,
    content: str,
    rrf_score: float,
    adjacent_context: bool = False,
) -> tuple[float, bool]:
    normalized_need = _coverage_normalize(need_query)
    normalized_content = _coverage_normalize(content)
    need_bigrams = _coverage_bigrams(normalized_need)
    content_bigrams = _coverage_bigrams(normalized_content)
    bigram_coverage = (
        len(need_bigrams.intersection(content_bigrams))
        / max(1, len(need_bigrams))
    )
    exact_match = bool(
        normalized_need and normalized_need in normalized_content
    )
    subject_marker = _COVERAGE_SUBJECT_MARKERS.get(need_type)
    subject = _coverage_normalize(
        subject_marker.sub("", need_query)
        if subject_marker is not None
        else need_query
    )
    subject_match = bool(
        len(subject) >= 2 and subject in normalized_content
    )
    answer_signal = _COVERAGE_ANSWER_SIGNALS.get(need_type)
    has_answer_signal = (
        True
        if answer_signal is None
        else bool(answer_signal.search(content))
    )
    score = (
        2.0 * float(exact_match)
        + 2.0 * bigram_coverage
        + 1.5 * float(subject_match)
        + 2.0 * max(0.0, float(rrf_score))
        + (
            ADJACENT_CONTEXT_COVERAGE_BONUS
            if adjacent_context
            else 0.0
        )
    )
    return score, has_answer_signal


def _coverage_normalize(value: str) -> str:
    return re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff]+",
        "",
        str(value or ""),
    ).casefold()


def _coverage_bigrams(value: str) -> set[str]:
    return {
        value[index : index + 2]
        for index in range(max(0, len(value) - 1))
    }


def _direct_alignment_length(*, query: str, content: str) -> int:
    normalized_query = _coverage_normalize(query)
    normalized_content = _coverage_normalize(content)
    maximum_width = min(
        _MAX_DIRECT_ALIGNMENT_CHARS,
        len(normalized_query),
        len(normalized_content),
    )
    for width in range(
        maximum_width,
        _MIN_DIRECT_ALIGNMENT_CHARS - 1,
        -1,
    ):
        for start in range(len(normalized_query) - width + 1):
            phrase = normalized_query[start : start + width]
            if not _is_informative_direct_alignment_phrase(phrase):
                continue
            if phrase in normalized_content:
                return width
    return 0


def _is_informative_direct_alignment_phrase(phrase: str) -> bool:
    normalized = _coverage_normalize(phrase)
    if len(normalized) < _MIN_DIRECT_ALIGNMENT_CHARS:
        return False
    if normalized in _DIRECT_ALIGNMENT_META_PHRASES:
        return False
    if _DIRECT_ALIGNMENT_NON_ENTITY_PATTERN.fullmatch(normalized):
        return False
    if _DIRECT_ALIGNMENT_RELATION_ONLY_PATTERN.fullmatch(normalized):
        return False
    return True


def _normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _split_clauses(query: str) -> list[str]:
    raw_parts = re.split(r"[。；;！？?!\r\n]+", query)
    clauses: list[str] = []
    for raw_part in raw_parts:
        clause = re.sub(
            r"^\s*(?:第?[一二三四五六七八九十]+|[0-9]+)"
            r"\s*[、.)）]\s*",
            "",
            raw_part,
        ).strip(" ，,、：:")
        if len(clause) < 4:
            continue
        _append_unique(clauses, clause, limit=10)
    return clauses


def _extract_predicate_aware_fact_needs(
    query: str,
    *,
    max_slot_count: int,
) -> list[_PredicateAwareNeed]:
    """Keep shared predicates attached to each enumerated subject."""

    normalized = _normalize_query(query).strip(" ？?。！!")
    bounded_count = max(2, min(int(max_slot_count), 6))
    without_leading_prompt = _PREDICATE_LEADING_SEPARATE_PATTERN.sub(
        "",
        normalized,
    ).strip()
    shared_match = _PREDICATE_SHARED_PATTERN.match(
        without_leading_prompt
    )
    if shared_match is not None:
        subjects = _split_predicate_subjects(
            shared_match.group("subjects")
        )
        predicate = _normalize_predicate_phrase(
            shared_match.group("predicate")
        )
        shared_context = ""
        if subjects:
            context_match = _PREDICATE_SHARED_CONTEXT_PATTERN.match(
                subjects[-1]
            )
            if context_match is not None:
                subjects[-1] = _clean_predicate_subject(
                    context_match.group("subject")
                )
                shared_context = _normalize_query(
                    context_match.group("context")
                )
        subjects = [item for item in subjects if len(_fingerprint(item)) >= 2]
        if len(subjects) >= 2:
            if (
                _PREDICATE_GENERIC_QUESTION_PATTERN.fullmatch(
                    predicate
                )
                and not shared_context
            ):
                needs = [
                    _predicate_need_from_fragment(
                        fragment=subject,
                        original_query=normalized,
                    )
                    for subject in subjects
                ]
                return _deduplicate_predicate_needs(
                    [item for item in needs if item is not None],
                    limit=bounded_count,
                )
            dimensions = _predicate_dimensions(
                " ".join(
                    item
                    for item in (shared_context, predicate)
                    if item
                )
            )
            needs: list[_PredicateAwareNeed] = []
            for subject in subjects:
                for dimension in dimensions:
                    need = _build_predicate_need(
                        subject=subject,
                        relation_phrase=dimension,
                    )
                    if need is not None:
                        needs.append(need)
                    if len(needs) >= bounded_count:
                        return _deduplicate_predicate_needs(
                            needs,
                            limit=bounded_count,
                        )
            if len(needs) >= 2:
                return _deduplicate_predicate_needs(
                    needs,
                    limit=bounded_count,
                )

    legacy_slots = _extract_atomic_fact_slots(
        normalized,
        max_slot_count=bounded_count,
    )
    fallback_needs = [
        _predicate_need_from_fragment(
            fragment=slot_query,
            original_query=normalized,
            fallback_type=slot_type,
        )
        for slot_query, slot_type in legacy_slots
    ]
    return _deduplicate_predicate_needs(
        [item for item in fallback_needs if item is not None],
        limit=bounded_count,
    )


def _split_predicate_subjects(value: str) -> list[str]:
    return [
        cleaned
        for item in _PREDICATE_SUBJECT_SPLIT_PATTERN.split(value)
        if (cleaned := _clean_predicate_subject(item))
    ]


def _clean_predicate_subject(value: str) -> str:
    cleaned = _normalize_query(value).strip(" ，,、：:")
    cleaned = re.sub(
        r"^(?:请|本项目的|本项目|项目的|项目)\s*",
        "",
        cleaned,
    )
    return cleaned


def _normalize_predicate_phrase(value: str) -> str:
    normalized = _normalize_query(value).strip(" ？?。！!")
    normalized = re.sub(r"^(?:请)?(?:说明|列明|写明|指出)", "", normalized)
    normalized = normalized.replace("包含什么", "包含")
    normalized = re.sub(
        r"哪些(.+?)不(?:包含|包括)",
        r"\1 不包含",
        normalized,
    )
    normalized = re.sub(r"需要完成哪些", "需要完成", normalized)
    normalized = re.sub(r"(?:需要|要)?提交什么", "提交", normalized)
    normalized = re.sub(r"哪些电子格式", "电子格式", normalized)
    normalized = re.sub(r"几份", "份数", normalized)
    return _normalize_query(normalized).strip(" ，,、：:")


def _predicate_dimensions(value: str) -> list[str]:
    normalized = _normalize_predicate_phrase(value)
    if not normalized:
        return []
    fragments = [
        _normalize_query(item).strip(" ，,、：:")
        for item in _PREDICATE_DIMENSION_SPLIT_PATTERN.split(
            normalized
        )
    ]
    fragments = [item for item in fragments if item]
    has_inclusion = any(
        re.search(r"包含|包括", item)
        and not re.search(r"不包含|不包括|不含|除外", item)
        for item in fragments
    )
    has_exclusion = any(
        re.search(r"不包含|不包括|不含|除外", item)
        for item in fragments
    )
    if has_inclusion and has_exclusion:
        return fragments
    return [normalized]


def _predicate_need_from_fragment(
    *,
    fragment: str,
    original_query: str,
    fallback_type: str | None = None,
) -> _PredicateAwareNeed | None:
    normalized = _normalize_query(fragment).strip(" ？?。！!，,、：:")
    if not normalized:
        return None
    relation_type = _predicate_relation_type(
        normalized,
        original_query=original_query,
        fallback_type=fallback_type,
    )
    return _build_predicate_need(
        subject=normalized,
        relation_phrase=normalized,
        relation_type=relation_type,
    )


def _build_predicate_need(
    *,
    subject: str,
    relation_phrase: str,
    relation_type: str | None = None,
) -> _PredicateAwareNeed | None:
    normalized_subject = _clean_predicate_subject(subject)
    normalized_relation = _normalize_predicate_phrase(relation_phrase)
    resolved_type = relation_type or _predicate_relation_type(
        normalized_relation,
        original_query=normalized_relation,
    )
    refined_subject = _clean_predicate_subject(
        _predicate_subject_from_fragment(
            normalized_subject,
            relation_type=resolved_type,
        )
    )
    if len(_fingerprint(refined_subject)) >= 2:
        normalized_subject = refined_subject
    if (
        len(_fingerprint(normalized_subject)) < 2
        or not normalized_relation
    ):
        return None
    query = _normalize_query(
        f"{normalized_subject} {normalized_relation}"
    )
    return _PredicateAwareNeed(
        query=query,
        subject=normalized_subject,
        relation_type=resolved_type,
        answer_shape=_predicate_answer_shape(
            relation_type=resolved_type,
            relation_phrase=normalized_relation,
        ),
    )


def _predicate_relation_type(
    value: str,
    *,
    original_query: str,
    fallback_type: str | None = None,
) -> str:
    combined = f"{value} {original_query}"
    if re.search(r"不包含|不包括|不含|除外", value):
        return "exclusion"
    if re.search(r"未提供|未包含|未附|缺少|缺失|补取|补充", combined):
        return "missing"
    if re.search(r"冲突|不一致|未同步|未回写|重复|漏报|更正|变更", combined):
        return "conflict"
    if re.search(r"负责|承担|配合|职责|由.+?单位", value):
        return "responsibility"
    if re.search(
        r"成果|文件|图纸|图册|材料表|电子版|电子格式|PDF|DWG|CAD",
        value,
        re.IGNORECASE,
    ):
        return "deliverable"
    inferred = _fact_slot_type(value)
    if inferred != "entity_fact":
        return inferred
    if fallback_type and fallback_type != "entity_fact":
        return fallback_type
    if re.search(r"工作与成果|工作成果|完成哪些工作", combined):
        return "deliverable"
    if re.search(r"负责|承担|配合|职责", combined):
        return "responsibility"
    return "entity_fact"


def _predicate_answer_shape(
    *,
    relation_type: str,
    relation_phrase: str,
) -> str:
    shapes = {
        "amount": ["amount_value"],
        "time": ["time_value"],
        "location": ["location_value"],
        "quantity": ["quantity_value"],
        "inclusion": ["inclusion_relation"],
        "exclusion": ["exclusion_relation"],
        "condition": ["condition_relation"],
        "requirement": ["requirement_relation"],
        "standard": ["standard_relation"],
        "responsibility": ["responsibility_relation"],
        "missing": ["missing_relation"],
        "conflict": ["conflict_relation"],
        "deliverable": ["deliverable"],
        "entity_fact": ["substantive_predicate"],
    }.get(relation_type, ["substantive_predicate"])
    if re.search(r"份数|套数|几份|几套", relation_phrase):
        shapes.append("quantity")
    if re.search(
        r"电子|PDF|DWG|CAD|格式",
        relation_phrase,
        re.IGNORECASE,
    ):
        shapes.append("format")
    if relation_type == "responsibility" and re.search(
        r"配合|协助|协调|预留",
        relation_phrase,
    ):
        shapes.append("collaboration")
    return "+".join(dict.fromkeys(shapes))


def _deduplicate_predicate_needs(
    needs: Sequence[_PredicateAwareNeed],
    *,
    limit: int,
) -> list[_PredicateAwareNeed]:
    unique: list[_PredicateAwareNeed] = []
    seen: set[tuple[str, str, str]] = set()
    for need in needs:
        if _PREDICATE_META_SUBJECT_PATTERN.fullmatch(
            _coverage_normalize(need.subject)
        ):
            continue
        key = (
            _fingerprint(need.subject),
            need.relation_type,
            need.answer_shape,
        )
        if key in seen:
            continue
        unique.append(need)
        seen.add(key)
        if len(unique) >= limit:
            break
    return unique if len(unique) >= 2 else []


def _predicate_subject_from_fragment(
    value: str,
    *,
    relation_type: str,
) -> str:
    normalized = _normalize_query(value).strip(" ？?。！!，,、：:")
    if relation_type == "responsibility":
        collaboration_marker = re.search(
            r"配合|协助|协调|预留",
            normalized,
        )
        if collaboration_marker is not None:
            collaboration_prefix = normalized[
                : collaboration_marker.start()
            ]
            collaboration_party = re.split(
                r"但需要|需要|需|由",
                collaboration_prefix,
            )[-1]
            collaboration_party = re.sub(
                r"负责$",
                "",
                collaboration_party,
            ).strip()
            if re.fullmatch(
                _PREDICATE_PARTY_TOKEN,
                collaboration_party,
            ):
                return collaboration_party
        responsibility_marker = re.search(r"负责", normalized)
        if responsibility_marker is not None:
            responsibility_prefix = normalized[
                : responsibility_marker.start()
            ]
            responsible_party = re.split(
                r"需要|需|由",
                responsibility_prefix,
            )[-1].strip()
            if re.fullmatch(
                _PREDICATE_PARTY_TOKEN,
                responsible_party,
            ):
                return responsible_party
    if relation_type == "missing":
        requested_object = re.search(
            r"(?:补取|补充|提供)哪些(?P<subject>[^，,；;。？?]+)",
            normalized,
        )
        if requested_object is not None:
            return requested_object.group("subject")
        cleaned = re.sub(
            r"(?:是否)?(?:已|已经)?(?:在当前资料中)?"
            r"(?:提供|包含|附有)|"
            r"(?:是否)?(?:缺少|缺失)$",
            "",
            normalized,
        ).strip(" 的，,、：:")
        if cleaned:
            return cleaned
    if relation_type == "conflict":
        changed_object = re.search(
            r"哪些(?P<subject>.+?)(?:未同步|未回写|"
            r"有冲突|不一致|发生变更|已变更)",
            normalized,
        )
        if changed_object is not None:
            return changed_object.group("subject")
        cleaned = re.sub(
            r"(?:是否)?(?:已经)?(?:完整)?"
            r"(?:存在)?(?:冲突|不一致|未同步|未回写)$",
            "",
            normalized,
        ).strip(" 的，,、：:")
        if cleaned:
            return cleaned
    marker = _PREDICATE_SUBJECT_MARKERS.get(relation_type)
    return _normalize_query(
        marker.sub("", normalized)
        if marker is not None
        else normalized
    ).strip(" ，,、：:")


def _extract_atomic_fact_slots(
    query: str,
    *,
    max_slot_count: int,
) -> list[tuple[str, str]]:
    """Split a compound question by surface structure, not business topic."""

    if not _COMPOUND_FACT_PATTERN.search(query):
        return []
    bounded_count = max(2, min(int(max_slot_count), 6))
    slots: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_fragment in _FACT_SLOT_SPLIT_PATTERN.split(query):
        normalized = _normalize_fact_slot_fragment(raw_fragment)
        if len(_fingerprint(normalized)) < 2:
            continue
        fingerprint = _fingerprint(normalized)
        if fingerprint in seen or fingerprint == _fingerprint(query):
            continue
        slots.append((normalized, _fact_slot_type(normalized)))
        seen.add(fingerprint)
        if len(slots) >= bounded_count:
            break
    return slots if len(slots) >= 2 else []


def _normalize_fact_slot_fragment(value: str) -> str:
    fragment = _normalize_query(value).strip(" ？?。！!")
    fragment = _FACT_SLOT_PREFIX_PATTERN.sub("", fragment).strip()
    fragment = re.sub(
        r"(?:要)?递交到哪里",
        "递交地点",
        fragment,
    )
    fragment = re.sub(
        r"(?:准备)?几套(.+)",
        r"\1 套数",
        fragment,
    )
    fragment = re.sub(
        r"几份(.+)",
        r"\1 份数",
        fragment,
    )
    fragment = re.sub(
        r"哪些(.+?)不(?:包含|包括)",
        r"\1 不包含",
        fragment,
    )
    fragment = re.sub(
        r"(.+?)(?:分别)?包含什么",
        r"\1 包含",
        fragment,
    )
    fragment = re.sub(
        r"(?:分别)?(?:是什么|有哪些|如何|是多少)$",
        "",
        fragment,
    )
    return _normalize_query(fragment).strip(" ，,、：:")


def _fact_slot_type(query: str) -> str:
    if re.search(r"不包含|不包括|除外", query):
        return "exclusion"
    if re.search(r"包含|包括", query):
        return "inclusion"
    if re.search(r"地点|哪里", query):
        return "location"
    if re.search(r"份数|套数|数量", query):
        return "quantity"
    if re.search(r"金额|价格|费用", query):
        return "amount"
    if re.search(r"截止|时间|期限|工期", query):
        return "time"
    if "要求" in query:
        return "requirement"
    if "条件" in query:
        return "condition"
    if re.search(r"标准|质量|验收|保修|质保", query):
        return "standard"
    return "entity_fact"


def _append_unique(values: list[str], value: str, *, limit: int) -> None:
    if len(values) >= limit:
        return
    normalized = _normalize_query(value)
    if not normalized:
        return
    fingerprint = _fingerprint(normalized)
    if any(_fingerprint(item) == fingerprint for item in values):
        return
    values.append(normalized)


def _fingerprint(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())
