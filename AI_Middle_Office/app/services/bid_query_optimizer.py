"""Deterministic, bounded query optimization for role-aware bid evidence search.

RQ1-C deliberately performs no model call and reads no document content.  It
turns one user/task query into a small, auditable set of lexical retrieval
queries while preserving the original query as the first immutable anchor.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from app.services.bid_assessment_eventing import canonical_hash
from mcp_servers.tender_evidence.query_planner import plan_tender_query


QUERY_PLAN_CONTRACT_VERSION = "bid.evidence.query-plan.v2"
QUERY_OPTIMIZER_PROFILE_VERSION = (
    "bid-evidence-query-optimizer-profile-v1-rq1c"
)
LEGACY_QUERY_PLANNER_PROFILE_VERSION = "tender-query-planner-v1"
DEFAULT_MAX_QUERY_COUNT = 6
MAX_QUERY_COUNT = 6
MAX_SUBJECT_COUNT = 4
MAX_QUERY_CHARS = 500


class BidQueryOptimizerError(ValueError):
    code = "BID_QUERY_OPTIMIZER_INVALID"


@dataclass(frozen=True)
class _FieldRule:
    code: str
    pattern: re.Pattern[str]
    terms: tuple[str, ...]
    answer_shape: str
    polarity: str = "neutral"


@dataclass(frozen=True)
class BidOptimizedQuery:
    query_id: str
    text: str
    kind: str
    weight: float
    field_codes: tuple[str, ...]
    answer_shapes: tuple[str, ...]
    reason_codes: tuple[str, ...]
    subject: str | None = None
    polarity: str = "neutral"

    def to_payload(self) -> dict[str, object]:
        return {
            "query_id": self.query_id,
            "text": self.text,
            "kind": self.kind,
            "weight": self.weight,
            "field_codes": list(self.field_codes),
            "answer_shapes": list(self.answer_shapes),
            "reason_codes": list(self.reason_codes),
            "subject": self.subject,
            "polarity": self.polarity,
        }


@dataclass(frozen=True)
class BidOptimizedQueryPlan:
    original_query: str
    query_items: tuple[BidOptimizedQuery, ...]
    detected_field_codes: tuple[str, ...]
    detected_subjects: tuple[str, ...]
    legacy_strategy: str
    warnings: tuple[str, ...]

    @property
    def queries(self) -> tuple[str, ...]:
        return tuple(item.text for item in self.query_items)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": QUERY_PLAN_CONTRACT_VERSION,
            "profile_version": QUERY_OPTIMIZER_PROFILE_VERSION,
            "strategy": "bounded_field_and_subject_expansion",
            "original_query": self.original_query,
            "queries": list(self.queries),
            "query_items": [item.to_payload() for item in self.query_items],
            "query_count": len(self.query_items),
            "expansion_count": max(0, len(self.query_items) - 1),
            "query_budget": MAX_QUERY_COUNT,
            "detected_field_codes": list(self.detected_field_codes),
            "detected_subjects": list(self.detected_subjects),
            "legacy_planner_profile_version": LEGACY_QUERY_PLANNER_PROFILE_VERSION,
            "legacy_strategy": self.legacy_strategy,
            "clarification_required": False,
            "warnings": list(self.warnings),
        }
        payload["plan_hash"] = canonical_hash(payload)
        return payload


_FIELD_RULES = (
    _FieldRule(
        "project.name",
        re.compile(r"工程名称|项目名称|招标项目|标的名称"),
        ("工程名称", "项目名称", "招标项目名称"),
        "entity_name",
    ),
    _FieldRule(
        "project.location",
        re.compile(r"工程地点|项目地点|建设地点|施工地点|工程地址"),
        ("工程地点", "项目地点", "建设地点", "施工地点", "地址"),
        "location",
    ),
    _FieldRule(
        "scope.area",
        re.compile(r"装修面积|建筑面积|施工面积|面积"),
        ("装修面积", "施工面积", "建筑面积", "平方米", "㎡"),
        "area",
    ),
    _FieldRule(
        "scope.region",
        re.compile(r"主要区域|施工区域|装修区域|工作区域"),
        ("主要区域", "施工区域", "装修区域", "工作区域"),
        "text",
    ),
    _FieldRule(
        "scope.inclusion",
        re.compile(r"招标范围|承包范围|施工范围|工作范围|包含|包括"),
        ("招标范围", "承包范围", "施工范围", "工作内容", "包括"),
        "list",
        "include",
    ),
    _FieldRule(
        "scope.exclusion",
        re.compile(r"不包含|不包括|不含|除外|排除"),
        ("承包范围", "不含", "不包括", "不包含", "除外"),
        "list",
        "exclude",
    ),
    _FieldRule(
        "contract.pricing_method",
        re.compile(r"承包和计价方式|承包方式|计价方式|综合单价|总价包干"),
        ("承包方式", "计价方式", "综合单价", "总价包干", "单价包干"),
        "text",
    ),
    _FieldRule(
        "contract.price_adjustment",
        re.compile(r"市场波动|价格调整|单价调整|调价|费率|汇率|政策.*变动"),
        ("综合单价", "价格调整", "市场波动", "人工费", "物价", "费率", "汇率"),
        "boolean",
    ),
    _FieldRule(
        "qualification.bidder",
        re.compile(r"投标人.*资质|施工专业承包资质|资格条件|资质要求"),
        ("投标人资质", "资格条件", "专业承包资质", "资质等级"),
        "qualification",
    ),
    _FieldRule(
        "qualification.manager_registration",
        re.compile(r"项目经理.*注册|注册资格|建造师资格|项目负责人.*资格"),
        ("项目经理", "项目负责人", "注册资格", "注册建造师", "建造师资格"),
        "qualification",
    ),
    _FieldRule(
        "qualification.manager_experience",
        re.compile(r"项目经理.*经验|施工管理经验|工作经验"),
        ("项目经理", "项目负责人", "施工管理经验", "工作经验", "年以上"),
        "duration",
    ),
    _FieldRule(
        "qualification.manager_performance",
        re.compile(r"类似项目经历|类似项目业绩|项目经理.*业绩"),
        ("项目经理", "项目负责人", "类似项目经历", "类似项目业绩", "完成项目"),
        "count",
    ),
    _FieldRule(
        "security.bid",
        re.compile(r"投标担保|投标保证金|投标保函"),
        ("投标担保", "投标保证金", "保证金金额", "银行保函", "现金保证金"),
        "money_and_method",
    ),
    _FieldRule(
        "security.performance",
        re.compile(r"履约担保|履约保证金|履约保函"),
        ("履约担保", "履约保证金", "履约保函", "中标价比例", "提交期限"),
        "ratio_and_duration",
    ),
    _FieldRule(
        "submission.alternative",
        re.compile(r"替代方案|备选方案"),
        ("替代方案", "备选方案", "允许", "不允许"),
        "boolean",
    ),
    _FieldRule(
        "submission.validity",
        re.compile(r"投标有效期"),
        ("投标有效期", "有效期", "天", "日历天"),
        "duration",
    ),
    _FieldRule(
        "submission.location",
        re.compile(r"投标文件.*地点|提交到什么地点|递交地点|送达地点"),
        ("投标文件", "提交地点", "递交地点", "送达地址", "项目部办公室"),
        "location",
    ),
    _FieldRule(
        "submission.deadline",
        re.compile(r"投标截止|递交截止|提交截止|截止日期|截止时间"),
        ("投标截止日期", "投标截止时间", "递交截止", "年", "月", "日", "时"),
        "datetime",
    ),
    _FieldRule(
        "submission.copy_count",
        re.compile(r"多少份|份数|正本|副本|电子标书"),
        ("投标文件", "份数", "正本", "副本", "电子标书", "商务标", "技术标"),
        "count",
    ),
    _FieldRule(
        "submission.issue_date",
        re.compile(r"发标时间|发标日期|电子发标|招标文件.*发出"),
        ("招标文件", "电子发标", "发标日期", "发标时间", "年", "月", "日"),
        "date",
    ),
    _FieldRule(
        "site.survey",
        re.compile(r"现场踏勘|现场勘察|踏勘"),
        ("现场踏勘", "统一组织", "不组织", "自行踏勘", "现场勘察"),
        "boolean",
    ),
    _FieldRule(
        "schedule.start_date",
        re.compile(r"开工日期|开工时间|暂定开工"),
        ("开工日期", "开工时间", "暂定开工", "年", "月", "日"),
        "date",
    ),
    _FieldRule(
        "schedule.duration",
        re.compile(r"合同工期|施工工期|工期.*多少|工期要求"),
        ("合同工期", "施工工期", "工期要求", "日历天", "天"),
        "duration",
    ),
    _FieldRule(
        "schedule.completion_acceptance",
        re.compile(r"完工日期|竣工日期|完工.*认定|竣工.*认定"),
        ("完工日期", "竣工日期", "验收", "验收证明书", "认定条件"),
        "condition",
    ),
    _FieldRule(
        "tax.vat_rate",
        re.compile(r"增值税|税率"),
        ("增值税", "税率", "含税", "%"),
        "ratio",
    ),
    _FieldRule(
        "payment.advance",
        re.compile(r"预付款"),
        ("预付款", "支付预付款", "无预付款", "不支付预付款"),
        "boolean_or_ratio",
    ),
    _FieldRule(
        "payment.progress",
        re.compile(r"进度款|进度支付|审核值"),
        ("进度款", "进度支付", "审核值", "支付比例", "%"),
        "ratio",
    ),
    _FieldRule(
        "payment.suspension",
        re.compile(r"暂停支付|累计.*支付|达到合同价"),
        ("累计支付", "合同价款", "暂停支付", "支付上限", "%"),
        "ratio",
    ),
    _FieldRule(
        "payment.settlement",
        re.compile(r"结算后|结算款|结算总价|结算.*付款"),
        ("结算款", "结算总价款", "支付比例", "付款比例", "%"),
        "ratio",
    ),
    _FieldRule(
        "payment.retention",
        re.compile(r"质保金|其余.*结算款|保修款|尾款"),
        ("质保金", "尾款", "保修期满", "返还", "扣留比例", "%"),
        "ratio_and_condition",
    ),
    _FieldRule(
        "warranty.period",
        re.compile(r"保修期|质保期|缺陷责任期"),
        ("质量缺陷保修期", "保修期", "质保期", "起算", "个月"),
        "duration_and_start",
    ),
    _FieldRule(
        "risk.claim_or_extension",
        re.compile(r"费用索赔|工期延长|索赔.*工期|工期.*索赔"),
        ("费用索赔", "增补", "工期延长", "申请", "不予批准", "不被批准"),
        "boolean",
        "negative_outcome",
    ),
    _FieldRule(
        "dispute.jurisdiction",
        re.compile(r"法院起诉|管辖法院|争议.*法院|诉讼"),
        ("合同争议", "管辖法院", "工程所在地", "法院起诉", "诉讼"),
        "location",
    ),
    _FieldRule(
        "quality.standard",
        re.compile(r"质量.*标准|按什么标准|验收标准|空气质量"),
        ("质量标准", "验收标准", "规范", "国家标准", "空气质量"),
        "standard",
    ),
    _FieldRule(
        "quality.testing_party",
        re.compile(r"检测.*委托|由谁委托|专项检测|第三方检测"),
        ("专项检测", "委托", "第三方", "检测资质", "检测单位"),
        "responsible_party",
    ),
    _FieldRule(
        "evaluation.method",
        re.compile(r"评标原则|评标办法|评标方法|综合评标|评分标准"),
        ("评标原则", "评标办法", "评标方法", "综合评标", "评分标准"),
        "method",
    ),
)

_TRAILING_QUESTION = re.compile(r"[？?。.!！]+$")
_SUBJECT_SPLIT = re.compile(r"\s*(?:、|，|,|；|;|以及|并且|同时|和|与|及)\s*")
_LEADING_FILLER = re.compile(r"^(?:请问|请说明|请列明|本次|本工程|该工程|该项目|项目中)\s*")
_GENERIC_SUBJECT = re.compile(r"^(?:工程|项目|招标|招标文件|合同|本次招标)$")


def _normalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return _TRAILING_QUESTION.sub("", normalized).strip()


def _fingerprint(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_query(value)
        key = _fingerprint(normalized)
        if not normalized or not key or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return tuple(result)


def _detected_rules(query: str) -> tuple[_FieldRule, ...]:
    return tuple(rule for rule in _FIELD_RULES if rule.pattern.search(query))


def _looks_like_field(value: str, rules: tuple[_FieldRule, ...]) -> bool:
    return any(rule.pattern.search(value) for rule in rules)


def _extract_parallel_subjects(
    query: str,
    rules: tuple[_FieldRule, ...],
) -> tuple[str, ...]:
    if "分别" not in query and "各自" not in query:
        return ()
    marker = "分别" if "分别" in query else "各自"
    prefix = query.split(marker, 1)[0]
    if "的" in prefix:
        prefix = prefix.split("的", 1)[0]
    prefix = _LEADING_FILLER.sub("", prefix).strip()
    candidates = _dedupe(_SUBJECT_SPLIT.split(prefix))
    if len(candidates) < 2:
        return ()
    if all(_looks_like_field(value, rules) for value in candidates):
        return ()
    subjects = tuple(
        value
        for value in candidates
        if 2 <= len(value) <= 30
        and not _GENERIC_SUBJECT.match(value)
    )
    return subjects[:MAX_SUBJECT_COUNT] if len(subjects) >= 2 else ()


def _compact_terms(rules: Iterable[_FieldRule], *, limit: int = 9) -> tuple[str, ...]:
    values: list[str] = []
    for rule in rules:
        for term in rule.terms:
            if term not in values:
                values.append(term)
            if len(values) >= limit:
                return tuple(values)
    return tuple(values)


def optimize_bid_evidence_query(
    query: str,
    *,
    max_query_count: int = DEFAULT_MAX_QUERY_COUNT,
) -> BidOptimizedQueryPlan:
    """Build a stable lexical query plan; original query always remains q1."""

    original = _normalize_query(query)
    if not original or len(original) > MAX_QUERY_CHARS:
        raise BidQueryOptimizerError("BID_EVIDENCE_QUERY_INVALID")
    bounded_count = max(1, min(int(max_query_count), MAX_QUERY_COUNT))
    rules = _detected_rules(original)
    subjects = _extract_parallel_subjects(original, rules)
    legacy = plan_tender_query(
        original,
        max_query_count=MAX_QUERY_COUNT,
        enable_semantic_fact_companion=True,
        enable_atomic_fact_slots=True,
    )
    items: list[BidOptimizedQuery] = []
    fingerprints: set[str] = set()

    def add(
        text: str,
        *,
        kind: str,
        weight: float,
        field_rules: Iterable[_FieldRule] = (),
        reason_codes: tuple[str, ...],
        subject: str | None = None,
        polarity: str = "neutral",
    ) -> None:
        if len(items) >= bounded_count:
            return
        normalized = _normalize_query(text)
        fingerprint = _fingerprint(normalized)
        if not normalized or not fingerprint or fingerprint in fingerprints:
            return
        selected_rules = tuple(field_rules)
        fingerprints.add(fingerprint)
        items.append(
            BidOptimizedQuery(
                query_id=f"q{len(items) + 1}",
                text=normalized,
                kind=kind,
                weight=weight,
                field_codes=tuple(rule.code for rule in selected_rules),
                answer_shapes=tuple(dict.fromkeys(rule.answer_shape for rule in selected_rules)),
                reason_codes=reason_codes,
                subject=subject,
                polarity=polarity,
            )
        )

    add(
        original,
        kind="original",
        weight=1.0,
        field_rules=rules,
        reason_codes=("original_query_anchor",),
    )

    if subjects and rules:
        shared_terms = _compact_terms(rules)
        polarity = (
            "exclude" if any(rule.polarity == "exclude" for rule in rules) else "neutral"
        )
        for subject in subjects:
            add(
                " ".join((subject, *shared_terms)),
                kind="parallel_subject",
                weight=1.0,
                field_rules=rules,
                reason_codes=("parallel_subject_decomposition", "field_alias_expansion"),
                subject=subject,
                polarity=polarity,
            )

    for rule in rules:
        add(
            " ".join(rule.terms),
            kind="field_alias",
            weight=0.9,
            field_rules=(rule,),
            reason_codes=("detected_tender_field", "bounded_alias_expansion"),
            polarity=rule.polarity,
        )

    for text in legacy.atomic_queries:
        add(
            text,
            kind="legacy_atomic",
            weight=0.85,
            reason_codes=("legacy_deterministic_reuse", "atomic_clause"),
        )
    for text in legacy.fact_slot_queries:
        add(
            text,
            kind="legacy_fact_slot",
            weight=0.85,
            reason_codes=("legacy_deterministic_reuse", "fact_slot"),
        )
    for text in legacy.supporting_queries:
        add(
            text,
            kind="semantic_companion",
            weight=0.75,
            reason_codes=("legacy_deterministic_reuse", "semantic_fact_companion"),
        )

    warnings: list[str] = []
    if not rules:
        warnings.append("NO_TENDER_FIELD_ALIAS_MATCH")
    if len(items) >= bounded_count and (
        len(rules) + len(subjects) + len(legacy.atomic_queries) + len(legacy.fact_slot_queries)
        >= bounded_count
    ):
        warnings.append("QUERY_EXPANSION_BUDGET_REACHED")

    plan = BidOptimizedQueryPlan(
        original_query=original,
        query_items=tuple(items),
        detected_field_codes=tuple(rule.code for rule in rules),
        detected_subjects=subjects,
        legacy_strategy=str(legacy.strategy),
        warnings=tuple(sorted(set(warnings))),
    )
    validate_bid_query_plan(plan.to_payload())
    return plan


def validate_bid_query_plan(payload: dict[str, object]) -> None:
    if payload.get("schema_version") != QUERY_PLAN_CONTRACT_VERSION:
        raise BidQueryOptimizerError("BID_QUERY_PLAN_CONTRACT_INVALID")
    if payload.get("profile_version") != QUERY_OPTIMIZER_PROFILE_VERSION:
        raise BidQueryOptimizerError("BID_QUERY_PLAN_PROFILE_INVALID")
    items = list(payload.get("query_items") or [])
    queries = list(payload.get("queries") or [])
    if not 1 <= len(items) <= MAX_QUERY_COUNT or len(items) != len(queries):
        raise BidQueryOptimizerError("BID_QUERY_PLAN_BUDGET_INVALID")
    if int(payload.get("query_count") or 0) != len(items):
        raise BidQueryOptimizerError("BID_QUERY_PLAN_COUNT_INVALID")
    expansion_count = payload.get("expansion_count")
    if not isinstance(expansion_count, int) or expansion_count != len(items) - 1:
        raise BidQueryOptimizerError("BID_QUERY_PLAN_COUNT_INVALID")
    if str(items[0].get("kind") or "") != "original":
        raise BidQueryOptimizerError("BID_QUERY_PLAN_ANCHOR_INVALID")
    if str(items[0].get("text") or "") != str(payload.get("original_query") or ""):
        raise BidQueryOptimizerError("BID_QUERY_PLAN_ANCHOR_INVALID")
    if queries != [str(item.get("text") or "") for item in items]:
        raise BidQueryOptimizerError("BID_QUERY_PLAN_QUERY_LIST_INVALID")
    if len({_fingerprint(str(value)) for value in queries}) != len(queries):
        raise BidQueryOptimizerError("BID_QUERY_PLAN_DUPLICATE_QUERY")
    for index, item in enumerate(items, 1):
        if str(item.get("query_id") or "") != f"q{index}":
            raise BidQueryOptimizerError("BID_QUERY_PLAN_ORDER_INVALID")
        if not 0.0 < float(item.get("weight") or 0.0) <= 1.0:
            raise BidQueryOptimizerError("BID_QUERY_PLAN_WEIGHT_INVALID")
    stable = dict(payload)
    received_hash = str(stable.pop("plan_hash", ""))
    if received_hash != canonical_hash(stable):
        raise BidQueryOptimizerError("BID_QUERY_PLAN_HASH_INVALID")
