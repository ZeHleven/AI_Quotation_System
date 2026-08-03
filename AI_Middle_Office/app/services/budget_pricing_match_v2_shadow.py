"""Read-only shadow matcher for enterprise-quota pricing experiments.

This module deliberately does not participate in the production quote cascade.
It produces ranked, explainable recommendations that can be reviewed against a
business-owned gold set before a future engine version is activated.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from difflib import SequenceMatcher
from typing import Any, Iterable

from app.services.budget_pricing import _QuotaEntry, _normalize_text, normalize_pricing_unit


SHADOW_MATCHING_ENGINE_VERSION = "budget-pricing-match-v2-shadow"
SHADOW_DECISION_AUTO = "shadow_auto"
SHADOW_DECISION_REVIEW = "shadow_review"
SHADOW_DECISION_NONE = "shadow_unmatched"

_Q6 = Decimal("0.000001")
_AUTO_SCORE = Decimal("0.760000")
_AUTO_MARGIN = Decimal("0.060000")
_REVIEW_SCORE = Decimal("0.500000")
_MAX_CANDIDATES = 5

_ACTION_REMOVE = "remove"
_ACTION_BUILD = "build"
_ACTION_NEUTRAL = "neutral"

_COUNT_UNITS = {"个", "套", "樘", "台", "组", "只", "座", "副"}
_DOOR_UNITS = {"套", "樘"}

_PHRASE_ALIASES: tuple[tuple[str, str], ...] = (
    ("清境墙面", "镜子墙面"),
    ("清镜墙面", "镜子墙面"),
    ("石膏板天花", "石膏板吊顶"),
    ("矿棉板天花", "矿棉板吊顶"),
    ("扣板天花", "扣板吊顶"),
    ("天花", "吊顶"),
    ("墙布", "壁布"),
    ("地砖", "地面瓷砖"),
    ("墙砖", "墙面瓷砖"),
    ("门槛石", "过门石"),
    ("排气扇", "换气扇"),
    ("台盆", "洗脸盆"),
    ("花洒", "淋浴器"),
    ("马桶", "座便器"),
    ("平级吊顶", "平顶吊顶"),
    ("造型吊顶", "跌级吊顶"),
    ("陶粒回填", "陶粒混凝土回填"),
    ("电气配管", "电线管敷设"),
    ("电气配线", "电线敷设"),
    ("给水管", "给水管道"),
    ("排水管", "排水管道"),
    ("供货及安装", "安装"),
    ("采购及安装", "安装"),
    ("制作及安装", "安装"),
    ("制作安装", "安装"),
    ("定制安装", "安装"),
)

_GENERIC_NAME_WORDS = (
    "成品",
    "定制",
    "供货",
    "采购",
    "制作",
    "安装",
    "敷设",
    "铺设",
    "铺贴",
    "拆除",
    "凿除",
    "处理",
    "工程",
)

_CONCEPT_PATTERNS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("remove", ("拆除", "凿除", "铲除"), 5),
    ("door", ("门", "门扇", "门套"), 4),
    ("single_leaf", ("单开", "单扇"), 5),
    ("double_leaf", ("双开", "双扇"), 5),
    ("wood_door", ("木门", "实木门", "木饰面门"), 5),
    ("glass_door", ("玻璃门", "地弹门"), 5),
    ("aluminum_door", ("铝合金门",), 5),
    ("floor_tile", ("地面瓷砖", "瓷砖地面"), 5),
    ("wall_tile", ("墙面瓷砖", "瓷砖墙面"), 5),
    ("wood_floor", ("木地板", "实木地板", "复合地板", "地板"), 4),
    ("stone_floor", ("石材地面",), 5),
    ("stone_wall", ("石材墙面", "墙面石材"), 5),
    ("crystallization", ("结晶",), 6),
    ("wet_lay", ("湿贴",), 6),
    ("ceiling", ("吊顶",), 3),
    ("wall_surface", ("墙面", "墙体"), 3),
    ("gypsum", ("石膏板",), 4),
    ("waterproof_gypsum", ("防水石膏板",), 5),
    ("mineral_board", ("矿棉板",), 5),
    ("aluminum_board", ("铝扣板",), 5),
    ("masonry", ("砌筑", "砌块墙", "砖砌"), 5),
    ("plaster", ("抹灰",), 5),
    ("backfill", ("回填",), 4),
    ("ceramsite", ("陶粒",), 5),
    ("waterproof", ("防水",), 4),
    ("protective_layer", ("保护层",), 4),
    ("threshold", ("过门石",), 5),
    ("grout", ("美缝", "填缝"), 5),
    ("light_trough", ("灯槽",), 5),
    ("curtain_box", ("窗帘盒",), 5),
    ("paint", ("涂料", "乳胶漆", "无机涂料"), 4),
    ("black", ("黑色",), 4),
    ("white", ("白色",), 4),
    ("glass_partition", ("玻璃隔墙", "玻璃隔断"), 5),
    ("wallcovering", ("壁布",), 5),
    ("hard_finish", ("硬包",), 5),
    ("wood_veneer", ("木饰面",), 5),
    ("column", ("包柱", "造型柱"), 6),
    ("windowsill", ("窗台石",), 6),
    ("metal_line", ("金属线条", "不锈钢线条"), 5),
    ("stainless", ("不锈钢",), 5),
    ("pvc", ("pvc",), 5),
    ("skirting", ("踢脚线",), 5),
    ("shower_partition", ("淋浴隔断", "淋浴屏"), 5),
    ("washstand", ("洗手台",), 5),
    ("door_frame", ("门套", "窗套"), 4),
    ("cleaning", ("保洁",), 5),
    ("protection", ("成品保护",), 5),
    ("transport", ("二次运输",), 5),
    ("distribution_box", ("配电箱",), 5),
    ("power_cable", ("电力电缆", "电缆"), 5),
    ("conduit", ("电线管", "配管"), 5),
    ("wire", ("电线", "配线", "导线"), 4),
    ("socket", ("插座",), 5),
    ("information_socket", ("信息插座", "网络插座", "电话插座"), 6),
    ("power_socket", ("五孔", "三孔", "空调插座", "热水器插座", "保护性插座"), 6),
    ("switch", ("开关",), 5),
    ("light_fixture", ("灯具", "筒灯", "射灯", "吸顶灯", "吊灯", "灯带"), 5),
    ("fan", ("换气扇",), 5),
    ("water_heater", ("热水器",), 5),
    ("water_supply_pipe", ("给水管道",), 5),
    ("drain_pipe", ("排水管道",), 5),
    ("pipe_insulation", ("管道保温",), 5),
    ("valve", ("阀门", "截止阀", "止回阀"), 5),
    ("water_meter", ("水表",), 5),
    ("floor_drain", ("地漏",), 5),
    ("toilet", ("座便器", "蹲便器"), 5),
    ("shower", ("淋浴器",), 5),
    ("basin", ("洗脸盆", "立柱盆"), 5),
    ("faucet", ("龙头", "水龙头"), 5),
    ("mirror", ("镜子", "梳妆镜"), 5),
)

_HARD_CONTRADICTIONS = (
    ("single_leaf", "double_leaf"),
    ("floor_tile", "wall_tile"),
    ("stone_floor", "stone_wall"),
    ("power_socket", "information_socket"),
    ("black", "white"),
    ("stainless", "pvc"),
    ("wall_surface", "ceiling"),
)

_SOURCE_REQUIRED_CONCEPTS = {
    "grout",
    "wet_lay",
    "column",
    "windowsill",
    "washstand",
}

_CANDIDATE_SPECIAL_OPERATIONS = {
    "crystallization",
}

_COUNT_CONTEXTS = {
    "door",
    "single_leaf",
    "double_leaf",
    "socket",
    "switch",
    "light_fixture",
    "fan",
    "water_heater",
    "valve",
    "water_meter",
    "floor_drain",
    "toilet",
    "shower",
    "basin",
    "faucet",
    "mirror",
    "shower_partition",
}


@dataclass(frozen=True)
class _TextFeatures:
    raw: str
    canonical: str
    concepts: frozenset[str]
    structured_tokens: frozenset[str]
    action: str


def _q6(value: Decimal) -> Decimal:
    return value.quantize(_Q6, rounding=ROUND_HALF_UP)


def _plain(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return text.replace("×", "x").replace("＊", "x")


def _canonical_name(value: Any) -> str:
    text = _plain(value)
    text = re.sub(r"\b(?:st|ct|wd|mr|mt|pm)[-_]?\d+\b", "", text, flags=re.IGNORECASE)
    for source, target in _PHRASE_ALIASES:
        text = text.replace(source, target)
    for word in _GENERIC_NAME_WORDS:
        text = text.replace(word, "")
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _action(value: Any) -> str:
    text = _plain(value)
    if any(word in text for word in ("拆除", "凿除", "铲除")):
        return _ACTION_REMOVE
    if any(
        word in text
        for word in (
            "安装",
            "敷设",
            "制作",
            "定制",
            "供货",
            "采购",
            "新建",
            "砌筑",
            "铺贴",
            "回填",
            "涂刷",
            "保洁",
            "保护",
            "运输",
        )
    ):
        return _ACTION_BUILD
    return _ACTION_NEUTRAL


def _structured_tokens(value: Any) -> frozenset[str]:
    text = _plain(value).replace(" ", "")
    tokens: set[str] = set()
    for raw in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:mm)?\s*厚", text):
        tokens.add(f"厚:{raw}")
    for raw in re.findall(r"\b(?:dn|sc|mt)\s*[-:]?\s*\d+\b", text, flags=re.IGNORECASE):
        tokens.add(re.sub(r"\s+", "", raw).upper())
    for raw in re.findall(r"\b\d+(?:\.\d+)?\s*a\b", text, flags=re.IGNORECASE):
        tokens.add(re.sub(r"\s+", "", raw).upper())
    for raw in re.findall(r"\b\d+(?:\.\d+)?\s*kw\b", text, flags=re.IGNORECASE):
        tokens.add(re.sub(r"\s+", "", raw).upper())
    for raw in re.findall(r"\d+(?:\.\d+)?x\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?", text):
        tokens.add(f"尺寸:{raw}")
    for raw in re.findall(r"\b(?:wdzc-)?(?:yjy|byj)[-\w+.*²]+", text, flags=re.IGNORECASE):
        tokens.add(raw.upper())
    return frozenset(tokens)


def _concepts(value: Any) -> frozenset[str]:
    text = _plain(value)
    for source, target in _PHRASE_ALIASES:
        text = text.replace(source, target)
    found = {
        key
        for key, patterns, _weight in _CONCEPT_PATTERNS
        if any(pattern in text for pattern in patterns)
    }
    return frozenset(found)


def _features(name: Any, spec: Any = None) -> _TextFeatures:
    combined = " ".join(part for part in (_plain(name), _plain(spec)) if part)
    action = _action(name)
    if action == _ACTION_NEUTRAL:
        action = _action(spec)
    return _TextFeatures(
        raw=combined,
        canonical=_canonical_name(name),
        concepts=_concepts(combined),
        structured_tokens=_structured_tokens(combined),
        action=action,
    )


def _bigrams(value: str) -> set[str]:
    if not value:
        return set()
    if len(value) == 1:
        return {value}
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _name_similarity(left: str, right: str) -> Decimal:
    if not left or not right:
        return Decimal("0")
    if left == right:
        return Decimal("1")
    sequence = Decimal(str(SequenceMatcher(None, left, right).ratio()))
    left_grams, right_grams = _bigrams(left), _bigrams(right)
    dice = (
        Decimal(2 * len(left_grams & right_grams)) / Decimal(len(left_grams) + len(right_grams))
        if left_grams and right_grams
        else Decimal("0")
    )
    result = max(sequence, dice)
    if min(len(left), len(right)) >= 2 and (left in right or right in left):
        result = max(result, Decimal("0.900000"))
    return _q6(result)


def _concept_score(source: _TextFeatures, candidate: _TextFeatures) -> Decimal:
    if not source.concepts:
        return _name_similarity(source.canonical, candidate.canonical)
    weights = {key: Decimal(weight) for key, _patterns, weight in _CONCEPT_PATTERNS}
    source_total = sum((weights.get(key, Decimal("1")) for key in source.concepts), Decimal("0"))
    candidate_total = sum((weights.get(key, Decimal("1")) for key in candidate.concepts), Decimal("0"))
    overlap = source.concepts & candidate.concepts
    overlap_weight = sum((weights.get(key, Decimal("1")) for key in overlap), Decimal("0"))
    recall = overlap_weight / source_total if source_total else Decimal("0")
    precision = overlap_weight / candidate_total if candidate_total else Decimal("0")
    return _q6(recall * Decimal("0.75") + precision * Decimal("0.25"))


def _structured_score(source: _TextFeatures, candidate: _TextFeatures) -> tuple[Decimal, list[str]]:
    source_tokens = source.structured_tokens
    candidate_tokens = candidate.structured_tokens
    if not source_tokens:
        return Decimal("0.500000"), []
    if not candidate_tokens:
        return Decimal("0.400000"), ["candidate_missing_structured_spec"]
    overlap = source_tokens & candidate_tokens
    if overlap:
        return _q6(Decimal(len(overlap)) / Decimal(len(source_tokens))), []
    return Decimal("0.050000"), ["structured_spec_conflict"]


def _unit_score(source_unit: Any, candidate_unit: Any, concepts: Iterable[str]) -> tuple[Decimal, str]:
    source_raw = _plain(source_unit).replace(" ", "")
    candidate_raw = _plain(candidate_unit).replace(" ", "")
    source_norm = normalize_pricing_unit(source_raw)
    candidate_norm = normalize_pricing_unit(candidate_raw)
    if source_norm and candidate_norm and source_norm == candidate_norm:
        return Decimal("1"), "exact"
    if not source_norm or not candidate_norm:
        return Decimal("0.400000"), "missing"
    concept_set = set(concepts)
    if source_raw in _DOOR_UNITS and candidate_raw in _DOOR_UNITS and "door" in concept_set:
        return Decimal("0.950000"), "door_count_family"
    if (
        source_raw in _COUNT_UNITS
        and candidate_raw in _COUNT_UNITS
        and concept_set & _COUNT_CONTEXTS
    ):
        return Decimal("0.850000"), "context_count_family"
    return Decimal("0"), "conflict"


def _contradictions(source: _TextFeatures, candidate: _TextFeatures) -> list[str]:
    reasons: list[str] = []
    if source.action == _ACTION_REMOVE and candidate.action != _ACTION_REMOVE:
        reasons.append("remove_vs_non_remove")
    if source.action != _ACTION_REMOVE and candidate.action == _ACTION_REMOVE:
        reasons.append("non_remove_vs_remove")
    for left, right in _HARD_CONTRADICTIONS:
        if left in source.concepts and right in candidate.concepts:
            reasons.append(f"{left}_vs_{right}")
        if right in source.concepts and left in candidate.concepts:
            reasons.append(f"{right}_vs_{left}")
    for concept in _SOURCE_REQUIRED_CONCEPTS:
        if concept in source.concepts and concept not in candidate.concepts:
            reasons.append(f"source_{concept}_missing")
    for concept in _CANDIDATE_SPECIAL_OPERATIONS:
        if concept in candidate.concepts and concept not in source.concepts:
            reasons.append(f"candidate_extra_{concept}")
    return reasons


def _professional_score(source_sheet: str, quota_code: str | None) -> tuple[Decimal, bool]:
    is_mep = "机电" in source_sheet or "电气" in source_sheet or "给排水" in source_sheet
    is_installation_quota = str(quota_code or "").upper().startswith("AZ")
    if is_mep and not is_installation_quota:
        return Decimal("0"), False
    return Decimal("1"), True


def _score_candidate(source: dict[str, Any], entry: _QuotaEntry) -> dict[str, Any] | None:
    source_features = _features(source.get("item_name"), source.get("spec"))
    candidate_features = _features(entry.item_name, entry.work_content)
    contradictions = _contradictions(source_features, candidate_features)
    if contradictions:
        return None
    professional_score, professional_allowed = _professional_score(
        str(source.get("source_sheet") or ""),
        entry.quota_code,
    )
    if not professional_allowed:
        return None
    unit_score, unit_rule = _unit_score(
        source.get("unit"),
        entry.unit,
        source_features.concepts | candidate_features.concepts,
    )
    if unit_score <= 0:
        return None
    name_score = _name_similarity(source_features.canonical, candidate_features.canonical)
    concept_score = _concept_score(source_features, candidate_features)
    if name_score < Decimal("0.180000") and concept_score < Decimal("0.300000"):
        return None
    structured_score, risk_flags = _structured_score(source_features, candidate_features)
    score = (
        name_score * Decimal("0.35")
        + concept_score * Decimal("0.30")
        + structured_score * Decimal("0.20")
        + unit_score * Decimal("0.10")
        + professional_score * Decimal("0.05")
    )
    exact_name = bool(
        source_features.canonical
        and source_features.canonical == candidate_features.canonical
    )
    if exact_name and unit_score >= Decimal("0.850000"):
        score = max(score, Decimal("0.920000"))
    score = _q6(min(score, Decimal("1")))
    reason_parts = [
        f"name={name_score}",
        f"concept={concept_score}",
        f"spec={structured_score}",
        f"unit={unit_rule}",
        "professional=compatible",
    ]
    if risk_flags:
        reason_parts.extend(risk_flags)
    return {
        "entry": entry,
        "score": score,
        "name_score": name_score,
        "concept_score": concept_score,
        "structured_score": structured_score,
        "unit_score": unit_score,
        "unit_rule": unit_rule,
        "exact_name": exact_name,
        "risk_flags": risk_flags,
        "reason": "; ".join(reason_parts),
    }


def shadow_match_source(source: dict[str, Any], catalog: list[_QuotaEntry]) -> dict[str, Any]:
    """Return a ranked V2 recommendation without selecting a production price."""

    records = [
        record
        for entry in catalog
        if (record := _score_candidate(source, entry)) is not None
    ]
    records.sort(
        key=lambda record: (
            -(record["score"] or Decimal("0")),
            record["entry"].quota_code or "\uffff",
            record["entry"].item_id,
        )
    )
    top_records = records[:_MAX_CANDIDATES]
    top = top_records[0] if top_records else None
    runner_up = top_records[1] if len(top_records) > 1 else None
    top_score = top["score"] if top else Decimal("0")
    runner_up_score = runner_up["score"] if runner_up else Decimal("0")
    margin = _q6(top_score - runner_up_score)
    decision = SHADOW_DECISION_NONE
    rule = "no_explainable_candidate"
    if top is not None and top_score >= _AUTO_SCORE and margin >= _AUTO_MARGIN:
        decision = SHADOW_DECISION_AUTO
        rule = "high_confidence_score_and_margin"
    elif top is not None and top["exact_name"] and top["unit_score"] >= Decimal("0.850000"):
        decision = SHADOW_DECISION_AUTO
        rule = "canonical_name_and_context_unit"
    elif top is not None and top_score >= _REVIEW_SCORE:
        decision = SHADOW_DECISION_REVIEW
        rule = "candidate_requires_business_review"
    return {
        "engine_version": SHADOW_MATCHING_ENGINE_VERSION,
        "decision": decision,
        "rule": rule,
        "selected": top if decision == SHADOW_DECISION_AUTO else None,
        "recommended": top,
        "candidates": top_records,
        "top_score": _q6(top_score),
        "runner_up_score": _q6(runner_up_score),
        "margin": margin,
        "thresholds": {
            "auto_score": str(_AUTO_SCORE),
            "auto_margin": str(_AUTO_MARGIN),
            "review_score": str(_REVIEW_SCORE),
        },
    }


def serialize_shadow_candidate(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    entry: _QuotaEntry = record["entry"]
    return {
        "quota_item_id": int(entry.item_id),
        "quota_code": entry.quota_code,
        "quota_name": entry.item_name,
        "quota_spec": entry.work_content,
        "quota_unit": entry.unit,
        "unit_price": str(entry.unit_price) if entry.unit_price is not None else None,
        "score": str(record["score"]),
        "name_score": str(record["name_score"]),
        "concept_score": str(record["concept_score"]),
        "structured_score": str(record["structured_score"]),
        "unit_score": str(record["unit_score"]),
        "unit_rule": record["unit_rule"],
        "risk_flags": list(record["risk_flags"]),
        "reason": record["reason"],
    }
