from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any


RISK_LEVEL_ORDER = {"low": 1, "medium": 2, "high": 3}
RISK_GRADE_V2_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4, "blocking": 5}

RISK_TYPE_LABELS = {
    "fixed_total_price": "总价包干风险",
    "omission_liability": "漏项责任风险",
    "no_price_adjustment": "价格不调风险",
    "advance_funding": "垫资/无预付款风险",
    "delayed_payment": "付款周期风险",
    "liquidated_damages": "违约金风险",
    "claim_time_limit": "签证索赔风险",
    "site_condition": "现场条件风险",
    "design_or_drawing_unclear": "图纸/范围不清风险",
    "material_brand_constraint": "材料品牌/供应约束",
    "bid_rejection": "废标/否决风险",
    "anonymous_bid": "暗标风险",
}

RISK_SUBTYPE_LABELS = {
    "bid_rejection_clause": "废标条件需逐条复核",
    "anonymous_bid_identity": "技术标暗标身份信息风险",
    "fixed_total_contract": "固定总价/总价包干边界",
    "unit_price_no_adjustment": "综合单价不调整",
    "measure_fee_no_adjustment": "措施费/开办费包干不调整",
    "market_price_no_adjustment": "人工材料价格波动不调整",
    "general_price_no_adjustment": "合同价款不调整",
    "missing_item_included": "清单漏项视为已包含",
    "preliminaries_included": "开办费/基本要求费用已包含",
    "quantity_or_item_tentative": "暂定工程量/暂定项目",
    "drawing_or_scope_unclear": "图纸或范围边界不清",
    "site_condition_claim_rejected": "现场条件误解不得索赔",
    "advance_payment_none": "无预付款/前期垫资",
    "claim_evidence_strict": "签证索赔资料要求严格",
    "counter_claim_or_deduction": "反索赔/扣款机制",
    "schedule_delay_penalty": "工期延误违约金",
    "quality_material_penalty": "质量/材料违约金",
    "termination_penalty": "解除合同/高额违约责任",
    "owner_supplied_material": "甲供材料供应边界",
    "owner_limited_brand": "甲限/指定品牌报价约束",
    "sample_or_approval": "样品封样/认质认价约束",
    "generic": "其他合同风险",
}

RISK_SUBTYPE_ACTIONS = {
    "bid_rejection_clause": "投标文件交付前建立废标条款检查表，逐条确认签章、格式、资质、报价和响应要求。",
    "anonymous_bid_identity": "技术标模板、附件、文件属性和图纸截图需执行敏感信息检查，避免出现公司名、人员名或企业标识。",
    "fixed_total_contract": "复核图纸、清单和范围完整性，报价中考虑包干风险，必要时转答疑确认包干边界。",
    "unit_price_no_adjustment": "预算需确认主材、人工和长周期材料波动风险，决定是否报价预留。",
    "measure_fee_no_adjustment": "复核措施费/开办费清单是否覆盖临设、赶工、二搬、检测、样板等费用。",
    "market_price_no_adjustment": "识别价格敏感材料和人工项，报价时加入风险系数或转答疑确认调差机制。",
    "general_price_no_adjustment": "法务和预算共同确认合同价款调整边界，避免后期变更或市场波动无法补偿。",
    "missing_item_included": "预算应对图纸、清单、技术要求做漏项复核，无法确认的范围转答疑或报价预留。",
    "preliminaries_included": "检查开办费/基本要求费是否已覆盖所有施工组织和现场管理成本。",
    "quantity_or_item_tentative": "复核暂定数量和按实结算口径，确认单价是否固定以及重计量规则。",
    "drawing_or_scope_unclear": "整理图纸缺失、范围边界和暂定做法问题，在答疑阶段要求甲方明确。",
    "site_condition_claim_rejected": "组织现场踏勘并记录限制条件，把道路、堆场、二搬、夜间施工等纳入报价。",
    "advance_payment_none": "测算垫资周期、资金占用和现金流压力，必要时提高风险预留或谨慎投标。",
    "claim_evidence_strict": "项目执行前建立签证索赔时限和资料清单，明确邮件/书面确认流程。",
    "counter_claim_or_deduction": "法务复核反索赔触发条件和扣款比例，报价及履约方案中预留管理措施。",
    "schedule_delay_penalty": "技术负责人复核工期计划和关键路径，确认违约金是否有上限及可减免条件。",
    "quality_material_penalty": "采购和技术需确认材料验收、替换、检测和质量整改责任，避免高额扣罚。",
    "termination_penalty": "管理层需确认解除合同触发条件、高额违约责任和转包替代费用承担。",
    "owner_supplied_material": "确认甲供材料到货责任、延误责任和现场配合边界。",
    "owner_limited_brand": "采购核对指定品牌档次、供货周期和价格水平，避免低估主材成本。",
    "sample_or_approval": "确认样品封样、认质认价和替代品牌审批流程，纳入采购计划。",
}

RISK_SUBTYPE_REVIEW_ROLES = {
    "bid_rejection_clause": ["经营", "法务"],
    "anonymous_bid_identity": ["经营", "技术"],
    "fixed_total_contract": ["预算", "法务"],
    "unit_price_no_adjustment": ["预算"],
    "measure_fee_no_adjustment": ["预算", "技术"],
    "market_price_no_adjustment": ["预算", "采购"],
    "general_price_no_adjustment": ["预算", "法务"],
    "missing_item_included": ["预算", "技术"],
    "preliminaries_included": ["预算"],
    "quantity_or_item_tentative": ["预算", "技术"],
    "drawing_or_scope_unclear": ["技术", "预算"],
    "site_condition_claim_rejected": ["技术", "项目"],
    "advance_payment_none": ["经营", "财务"],
    "claim_evidence_strict": ["法务", "项目"],
    "counter_claim_or_deduction": ["法务", "项目"],
    "schedule_delay_penalty": ["技术", "项目"],
    "quality_material_penalty": ["技术", "采购"],
    "termination_penalty": ["法务", "经营"],
    "owner_supplied_material": ["采购", "项目"],
    "owner_limited_brand": ["采购", "预算"],
    "sample_or_approval": ["采购", "技术"],
}

RISK_SUBTYPE_PRIMARY_ACTION = {
    "bid_rejection_clause": "manual_blocking_review",
    "anonymous_bid_identity": "manual_blocking_review",
    "fixed_total_contract": "to_quote_allowance",
    "unit_price_no_adjustment": "to_quote_allowance",
    "measure_fee_no_adjustment": "to_quote_allowance",
    "market_price_no_adjustment": "to_quote_allowance",
    "general_price_no_adjustment": "to_quote_allowance",
    "missing_item_included": "to_clarify",
    "preliminaries_included": "to_quote_allowance",
    "quantity_or_item_tentative": "confirmed",
    "drawing_or_scope_unclear": "to_clarify",
    "site_condition_claim_rejected": "to_quote_allowance",
    "advance_payment_none": "to_quote_allowance",
    "claim_evidence_strict": "confirmed",
    "counter_claim_or_deduction": "to_quote_allowance",
    "schedule_delay_penalty": "to_quote_allowance",
    "quality_material_penalty": "to_quote_allowance",
    "termination_penalty": "to_clarify",
    "owner_supplied_material": "to_clarify",
    "owner_limited_brand": "to_quote_allowance",
    "sample_or_approval": "confirmed",
}

RISK_SUBTYPE_SECONDARY_ACTION = {
    "fixed_total_contract": "to_clarify",
    "unit_price_no_adjustment": "to_clarify",
    "measure_fee_no_adjustment": "to_clarify",
    "market_price_no_adjustment": "to_clarify",
    "general_price_no_adjustment": "to_clarify",
    "missing_item_included": "to_quote_allowance",
    "preliminaries_included": "to_clarify",
    "quantity_or_item_tentative": "to_clarify",
    "drawing_or_scope_unclear": "to_quote_allowance",
    "site_condition_claim_rejected": "to_clarify",
    "advance_payment_none": "bid_decision_review",
    "claim_evidence_strict": "to_clarify",
    "counter_claim_or_deduction": "to_clarify",
    "schedule_delay_penalty": "to_clarify",
    "quality_material_penalty": "to_clarify",
    "termination_penalty": "bid_decision_review",
    "owner_supplied_material": "to_quote_allowance",
    "owner_limited_brand": "to_clarify",
    "sample_or_approval": "to_clarify",
}

RISK_SUBTYPE_V2_PROFILES = {
    "bid_rejection_clause": {
        "base_score": 96,
        "grade_floor": "blocking",
        "drivers": ["废标/否决投标"],
        "reason": "命中废标或否决投标条款，属于投标文件交付前必须人工确认的阻断风险。",
        "scores": {"compliance": 96, "commercial": 20, "scope": 10, "claim": 10, "actionability": 95},
    },
    "anonymous_bid_identity": {
        "base_score": 94,
        "grade_floor": "blocking",
        "drivers": ["暗标身份信息限制"],
        "reason": "暗标文件出现单位、人员或企业标识可能直接导致否决投标，需作为阻断项处理。",
        "scores": {"compliance": 94, "commercial": 15, "scope": 10, "claim": 10, "actionability": 95},
    },
    "fixed_total_contract": {
        "base_score": 76,
        "grade_floor": "high",
        "drivers": ["固定总价/总价包干"],
        "reason": "固定总价或总价包干会把清单、范围和市场波动风险前置到报价阶段。",
        "scores": {"compliance": 20, "commercial": 78, "scope": 55, "claim": 35, "actionability": 70},
    },
    "unit_price_no_adjustment": {
        "base_score": 74,
        "grade_floor": "high",
        "drivers": ["综合单价不调整"],
        "reason": "综合单价不调整会放大人工、材料、措施和工程量偏差对利润的影响。",
        "scores": {"compliance": 15, "commercial": 76, "scope": 40, "claim": 30, "actionability": 68},
    },
    "measure_fee_no_adjustment": {
        "base_score": 72,
        "grade_floor": "high",
        "drivers": ["措施费/开办费包干"],
        "reason": "措施费或开办费包干容易遗漏临设、赶工、二搬、检测、样板等现场成本。",
        "scores": {"compliance": 15, "commercial": 74, "scope": 58, "claim": 28, "actionability": 70},
    },
    "market_price_no_adjustment": {
        "base_score": 73,
        "grade_floor": "high",
        "drivers": ["人工材料价格波动不调整"],
        "reason": "价格波动不调整会直接影响主材、人工和长周期采购项目的报价安全边界。",
        "scores": {"compliance": 15, "commercial": 78, "scope": 35, "claim": 28, "actionability": 72},
    },
    "general_price_no_adjustment": {
        "base_score": 70,
        "grade_floor": "high",
        "drivers": ["合同价款不调整"],
        "reason": "合同价款不调整会压缩后续变更、签证和市场波动的补偿空间。",
        "scores": {"compliance": 15, "commercial": 72, "scope": 40, "claim": 38, "actionability": 68},
    },
    "missing_item_included": {
        "base_score": 75,
        "grade_floor": "high",
        "drivers": ["清单漏项视为已包含"],
        "reason": "漏项、错项或特征不完整由投标人承担时，低价中标后的亏损暴露明显。",
        "scores": {"compliance": 15, "commercial": 74, "scope": 75, "claim": 45, "actionability": 78},
    },
    "preliminaries_included": {
        "base_score": 66,
        "grade_floor": "medium",
        "drivers": ["开办费/基本要求费用已包含"],
        "reason": "开办费或基本要求费用已包含，需要复核现场组织和管理成本是否完整覆盖。",
        "scores": {"compliance": 10, "commercial": 68, "scope": 58, "claim": 25, "actionability": 64},
    },
    "quantity_or_item_tentative": {
        "base_score": 52,
        "grade_floor": "medium",
        "drivers": ["暂定工程量/暂定项目"],
        "reason": "暂定量本身不一定高危，但需要确认结算、重计量和单价锁定规则。",
        "scores": {"compliance": 10, "commercial": 45, "scope": 65, "claim": 32, "actionability": 55},
    },
    "drawing_or_scope_unclear": {
        "base_score": 62,
        "grade_floor": "medium",
        "drivers": ["图纸或范围边界不清"],
        "reason": "图纸、范围或界面边界不清会影响报价完整性和后续签证空间。",
        "scores": {"compliance": 10, "commercial": 55, "scope": 75, "claim": 48, "actionability": 70},
    },
    "site_condition_claim_rejected": {
        "base_score": 70,
        "grade_floor": "high",
        "drivers": ["现场条件误解不得索赔"],
        "reason": "现场条件误解不得索赔会把踏勘不足、运输、堆场和施工限制转成承包人风险。",
        "scores": {"compliance": 10, "commercial": 65, "scope": 80, "claim": 68, "actionability": 72},
    },
    "advance_payment_none": {
        "base_score": 78,
        "grade_floor": "high",
        "drivers": ["无预付款/垫资"],
        "reason": "无预付款会带来前期垫资和现金流压力，需要经营和财务共同测算。",
        "scores": {"compliance": 10, "commercial": 82, "scope": 30, "claim": 25, "actionability": 72},
    },
    "claim_evidence_strict": {
        "base_score": 60,
        "grade_floor": "medium",
        "drivers": ["签证索赔证据要求严格"],
        "reason": "签证索赔时限或资料要求严格，执行阶段需要提前建立证据闭环。",
        "scores": {"compliance": 10, "commercial": 48, "scope": 35, "claim": 78, "actionability": 60},
    },
    "counter_claim_or_deduction": {
        "base_score": 68,
        "grade_floor": "medium",
        "drivers": ["反索赔/扣款机制"],
        "reason": "反索赔、整改扣款或第三方费用转嫁会提高履约阶段的损失不确定性。",
        "scores": {"compliance": 10, "commercial": 65, "scope": 40, "claim": 76, "actionability": 68},
    },
    "schedule_delay_penalty": {
        "base_score": 73,
        "grade_floor": "high",
        "drivers": ["工期延误违约金"],
        "reason": "工期违约金会放大工期组织、关键路径和甲供协同失败的履约责任。",
        "scores": {"compliance": 10, "commercial": 72, "scope": 45, "claim": 55, "actionability": 70},
    },
    "quality_material_penalty": {
        "base_score": 70,
        "grade_floor": "high",
        "drivers": ["质量/材料违约责任"],
        "reason": "质量、材料或检测违约责任会影响采购替代、验收和整改成本。",
        "scores": {"compliance": 12, "commercial": 68, "scope": 52, "claim": 58, "actionability": 68},
    },
    "termination_penalty": {
        "base_score": 84,
        "grade_floor": "critical",
        "drivers": ["解除合同/高额违约责任"],
        "reason": "解除合同或高额违约责任可能造成重大履约损失，需要投标决策层复核。",
        "scores": {"compliance": 20, "commercial": 88, "scope": 50, "claim": 72, "actionability": 78},
    },
    "owner_supplied_material": {
        "base_score": 58,
        "grade_floor": "medium",
        "drivers": ["甲供材料供应边界"],
        "reason": "甲供材料需要明确到货、延误、保管和配合责任，否则影响工期与现场组织。",
        "scores": {"compliance": 8, "commercial": 52, "scope": 60, "claim": 52, "actionability": 62},
    },
    "owner_limited_brand": {
        "base_score": 66,
        "grade_floor": "medium",
        "drivers": ["甲限/指定品牌"],
        "reason": "甲限或指定品牌会影响采购价格、供货周期和替代方案空间。",
        "scores": {"compliance": 10, "commercial": 68, "scope": 45, "claim": 35, "actionability": 66},
    },
    "sample_or_approval": {
        "base_score": 52,
        "grade_floor": "medium",
        "drivers": ["样品封样/认质认价"],
        "reason": "样品封样或认质认价需要纳入采购计划，避免后续审批影响进度。",
        "scores": {"compliance": 8, "commercial": 48, "scope": 42, "claim": 35, "actionability": 58},
    },
    "generic": {
        "base_score": 45,
        "grade_floor": "medium",
        "drivers": ["其他合同风险"],
        "reason": "该风险需要人工确认影响范围后再确定处置动作。",
        "scores": {"compliance": 10, "commercial": 45, "scope": 35, "claim": 35, "actionability": 45},
    },
}


def _compact_text(value: str | None) -> str:
    return re.sub(r"[\s\u3000:：,，、。.\-_/\\()（）【】\[\]「」《》]+", "", value or "")


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _risk_subtype(risk: Any) -> str:
    risk_type = getattr(risk, "risk_type", "") or ""
    text = _compact_text(getattr(risk, "original_text", "") or "")
    if risk_type == "bid_rejection":
        return "bid_rejection_clause"
    if risk_type == "anonymous_bid":
        return "anonymous_bid_identity"
    if risk_type == "fixed_total_price":
        return "fixed_total_contract"
    if risk_type == "no_price_adjustment":
        if _has_any(text, ("措施项目费", "开办费", "措施费")):
            return "measure_fee_no_adjustment"
        if _has_any(text, ("人工费", "材料费", "物价", "费率", "汇率", "市场价格", "价格涨幅", "价格波动")):
            return "market_price_no_adjustment"
        if _has_any(text, ("综合单价", "单价")):
            return "unit_price_no_adjustment"
        return "general_price_no_adjustment"
    if risk_type == "omission_liability":
        if _has_any(text, ("开办费", "基本要求费用")):
            return "preliminaries_included"
        return "missing_item_included"
    if risk_type == "design_or_drawing_unclear":
        if _has_any(text, ("踏勘", "现场条件", "工地位置", "道路", "储存空间", "装卸限制")):
            return "site_condition_claim_rejected"
        if _has_any(text, ("暂定", "暂估", "暂列", "暂定数量", "暂定合同总价")):
            return "quantity_or_item_tentative"
        return "drawing_or_scope_unclear"
    if risk_type == "advance_funding":
        return "advance_payment_none"
    if risk_type == "claim_time_limit":
        if _has_any(text, ("反索赔", "扣款", "第三方", "整改通知")):
            return "counter_claim_or_deduction"
        return "claim_evidence_strict"
    if risk_type == "liquidated_damages":
        if _has_any(text, ("解除合同", "单方解除", "20%")):
            return "termination_penalty"
        if _has_any(text, ("材料", "假冒伪劣", "检验", "质量", "整改")):
            return "quality_material_penalty"
        return "schedule_delay_penalty"
    if risk_type == "material_brand_constraint":
        if _has_any(text, ("甲供", "独立供应")):
            return "owner_supplied_material"
        if _has_any(text, ("甲限", "指定品牌", "品牌范围", "指定的品牌")):
            return "owner_limited_brand"
        if _has_any(text, ("样品", "封样", "认质认价", "认可")):
            return "sample_or_approval"
        return "owner_limited_brand"
    return "generic"


def _max_risk_level(risks: list[Any]) -> str:
    return max((getattr(risk, "risk_level", None) or "medium" for risk in risks), key=lambda level: RISK_LEVEL_ORDER.get(level, 2))


def _aggregate_review_status(risks: list[Any]) -> str:
    statuses = [getattr(risk, "review_status", None) or "pending" for risk in risks]
    counts = Counter(statuses)
    if counts.get("pending"):
        return "pending"
    if len(counts) == 1:
        return statuses[0]
    if counts.get("to_clarify"):
        return "to_clarify"
    if counts.get("to_quote_allowance"):
        return "to_quote_allowance"
    if counts.get("confirmed"):
        return "confirmed"
    return "mixed"


def _card_id(parse_run_id: int | None, risk_type: str, risk_subtype: str) -> str:
    raw = f"{parse_run_id or 0}|{risk_type}|{risk_subtype}"
    return f"risk-card-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def _risk_grade_v2_from_score(score: int, grade_floor: str = "low") -> str:
    if grade_floor == "blocking":
        return "blocking"
    if score >= 85:
        calculated = "critical"
    elif score >= 70:
        calculated = "high"
    elif score >= 45:
        calculated = "medium"
    else:
        calculated = "low"
    return max((grade_floor, calculated), key=lambda grade: RISK_GRADE_V2_ORDER.get(grade, 1))


def _group_text(risks: list[Any]) -> str:
    return "\n".join(str(getattr(risk, "original_text", "") or "") for risk in risks)


def _has_number_or_ratio(text: str) -> bool:
    return bool(re.search(r"\d+(\.\d+)?\s*(%|％|天|日|元|万元|小时|h|H|‰)?", text or ""))


def _dedupe_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _evaluate_risk_grade_v2(
    *,
    risk_type: str,
    risk_subtype: str,
    risks: list[Any],
    source_count: int,
    is_blocking: bool,
) -> dict[str, Any]:
    profile = RISK_SUBTYPE_V2_PROFILES.get(risk_subtype) or RISK_SUBTYPE_V2_PROFILES["generic"]
    text = _group_text(risks)
    has_number = _has_number_or_ratio(text)
    evidence_bonus = min(10, max(0, len(risks) - 1) * 2 + min(source_count, 4))
    if has_number:
        evidence_bonus += 3
    base_score = int(profile["base_score"])
    score = min(100, base_score + evidence_bonus)
    grade_floor = str(profile.get("grade_floor") or "low")
    if is_blocking or risk_type in {"bid_rejection", "anonymous_bid"}:
        grade_floor = "blocking"
        score = max(score, 94)
    grade = _risk_grade_v2_from_score(score, grade_floor)

    drivers = list(profile.get("drivers") or [])
    if len(risks) >= 3:
        drivers.append("多处条款重复出现")
    if source_count >= 3:
        drivers.append("来源位置分散")
    if has_number:
        drivers.append("包含明确数字/比例/时限")

    scores = dict(profile.get("scores") or {})
    scores["evidence"] = min(100, 45 + min(len(risks), 6) * 6 + min(source_count, 6) * 5 + (8 if has_number else 0))

    return {
        "risk_grade_v2": grade,
        "risk_score": score,
        "grade_reason": str(profile.get("reason") or ""),
        "primary_action": RISK_SUBTYPE_PRIMARY_ACTION.get(risk_subtype, "confirmed"),
        "secondary_action": RISK_SUBTYPE_SECONDARY_ACTION.get(risk_subtype),
        "review_roles": RISK_SUBTYPE_REVIEW_ROLES.get(risk_subtype, ["经营"]),
        "drivers": _dedupe_list(drivers),
        "score_dimensions": scores,
    }


def _bump_card_grade_v2(
    card: dict[str, Any],
    *,
    min_grade: str,
    min_score: int,
    driver: str,
    reason: str,
    primary_action: str | None = None,
    secondary_action: str | None = None,
) -> None:
    if RISK_GRADE_V2_ORDER.get(str(card.get("risk_grade_v2")), 1) < RISK_GRADE_V2_ORDER.get(min_grade, 1):
        card["risk_grade_v2"] = min_grade
    card["risk_score"] = max(int(card.get("risk_score") or 0), min_score)
    drivers = list(card.get("drivers") or [])
    if driver not in drivers:
        drivers.append(driver)
    card["drivers"] = drivers
    existing_reason = str(card.get("grade_reason") or "")
    if reason and reason not in existing_reason:
        card["grade_reason"] = f"{existing_reason} {reason}".strip()
    if primary_action:
        card["primary_action"] = primary_action
    if secondary_action:
        card["secondary_action"] = secondary_action


def _apply_cross_card_v2_escalations(cards: list[dict[str, Any]]) -> None:
    by_subtype = {str(card.get("risk_subtype")): card for card in cards}
    no_adjustment_subtypes = {
        "unit_price_no_adjustment",
        "measure_fee_no_adjustment",
        "market_price_no_adjustment",
        "general_price_no_adjustment",
    }
    omission_subtypes = {"missing_item_included", "preliminaries_included"}
    total_price_combo = (
        "fixed_total_contract" in by_subtype
        and any(subtype in by_subtype for subtype in no_adjustment_subtypes)
        and any(subtype in by_subtype for subtype in omission_subtypes)
    )
    if total_price_combo:
        reason = "同时存在总价包干、不调价和漏项责任，单项风险叠加后利润暴露升为重大风险。"
        for subtype in {"fixed_total_contract"} | no_adjustment_subtypes | omission_subtypes:
            card = by_subtype.get(subtype)
            if card:
                _bump_card_grade_v2(
                    card,
                    min_grade="critical",
                    min_score=88,
                    driver="总价包干+不调价+漏项责任组合",
                    reason=reason,
                    primary_action="to_quote_allowance",
                    secondary_action="to_clarify",
                )

    cash_penalty_combo = "advance_payment_none" in by_subtype and any(
        subtype in by_subtype
        for subtype in {"schedule_delay_penalty", "quality_material_penalty", "termination_penalty"}
    )
    if cash_penalty_combo:
        reason = "无预付款与违约责任同时出现，现金流和履约赔付风险叠加，需要经营层复核。"
        for subtype in {"advance_payment_none", "schedule_delay_penalty", "quality_material_penalty", "termination_penalty"}:
            card = by_subtype.get(subtype)
            if card:
                _bump_card_grade_v2(
                    card,
                    min_grade="critical",
                    min_score=86,
                    driver="现金流+违约责任组合",
                    reason=reason,
                    primary_action="to_quote_allowance",
                    secondary_action="bid_decision_review",
                )

    for card in cards:
        card["priority_score"] = _priority_score(
            risk_count=int(card.get("risk_count") or 0),
            grade_v2=str(card.get("risk_grade_v2") or "medium"),
            risk_score=int(card.get("risk_score") or 0),
            is_blocking=bool(card.get("is_blocking")),
        )


def _priority_score(*, risk_count: int, grade_v2: str, risk_score: int, is_blocking: bool) -> int:
    score = RISK_GRADE_V2_ORDER.get(grade_v2, 2) * 1000 + risk_score * 10
    if is_blocking or grade_v2 == "blocking":
        score += 500
    score += min(risk_count, 30)
    return score


def cluster_tender_risks_to_cards(risks: list[Any], *, parse_run_id: int | None = None) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for risk in sorted(risks, key=lambda item: (getattr(item, "risk_type", "") or "", getattr(item, "id", 0) or 0)):
        risk_type = getattr(risk, "risk_type", "") or "unknown"
        groups[(risk_type, _risk_subtype(risk))].append(risk)

    cards: list[dict[str, Any]] = []
    for (risk_type, risk_subtype), group in groups.items():
        level = _max_risk_level(group)
        is_blocking = any(bool(getattr(risk, "is_blocking", False)) for risk in group)
        first = group[0]
        review_status = _aggregate_review_status(group)
        source_locations = []
        source_files = []
        document_sections = []
        for risk in group:
            source_location = getattr(risk, "source_location", None)
            if source_location and source_location not in source_locations:
                source_locations.append(source_location)
            source_file = getattr(risk, "source_file", None)
            if source_file and source_file not in source_files:
                source_files.append(source_file)
            document_section = getattr(risk, "document_section", None)
            if document_section and document_section not in document_sections:
                document_sections.append(document_section)

        evidence = []
        for risk in sorted(group, key=lambda item: (0 if getattr(item, "risk_level", "") == "high" else 1, getattr(item, "id", 0) or 0))[:5]:
            evidence.append(
                {
                    "risk_uuid": getattr(risk, "risk_uuid", None),
                    "risk_level": getattr(risk, "risk_level", None),
                    "review_status": getattr(risk, "review_status", None),
                    "source_file": getattr(risk, "source_file", None),
                    "source_location": getattr(risk, "source_location", None),
                    "original_text": getattr(risk, "original_text", None),
                }
            )
        grade_v2 = _evaluate_risk_grade_v2(
            risk_type=risk_type,
            risk_subtype=risk_subtype,
            risks=group,
            source_count=len(source_locations),
            is_blocking=is_blocking,
        )

        cards.append(
            {
                "card_id": _card_id(parse_run_id, risk_type, risk_subtype),
                "risk_type": risk_type,
                "risk_subtype": risk_subtype,
                "title": RISK_SUBTYPE_LABELS.get(risk_subtype) or RISK_TYPE_LABELS.get(risk_type) or risk_type,
                "risk_level": level,
                "is_blocking": is_blocking,
                "review_status": review_status,
                "risk_count": len(group),
                "source_count": len(source_locations),
                "source_locations": source_locations[:12],
                "source_files": source_files[:4],
                "document_sections": document_sections,
                "impact_area": getattr(first, "impact_area", None),
                "risk_explanation": getattr(first, "risk_explanation", None),
                "suggested_action": RISK_SUBTYPE_ACTIONS.get(risk_subtype) or getattr(first, "suggested_action", None),
                "member_risk_uuids": [getattr(risk, "risk_uuid", None) for risk in group if getattr(risk, "risk_uuid", None)],
                "evidence": evidence,
                "confidence": round(sum(float(getattr(risk, "confidence", 0.6) or 0.6) for risk in group) / max(len(group), 1), 4),
                **grade_v2,
            }
        )

    _apply_cross_card_v2_escalations(cards)
    return sorted(cards, key=lambda item: (-int(item["priority_score"]), item["title"]))


def build_risk_card_summary(cards: list[dict[str, Any]], *, risk_count: int) -> dict[str, Any]:
    card_by_grade_v2 = Counter(card.get("risk_grade_v2") or "medium" for card in cards)
    return {
        "risk_count": risk_count,
        "card_count": len(cards),
        "high_card_count": sum(1 for card in cards if card.get("risk_level") == "high"),
        "blocking_card_count": sum(1 for card in cards if card.get("is_blocking")),
        "blocking_v2_card_count": card_by_grade_v2.get("blocking", 0),
        "critical_card_count": card_by_grade_v2.get("critical", 0),
        "high_v2_card_count": card_by_grade_v2.get("high", 0),
        "medium_v2_card_count": card_by_grade_v2.get("medium", 0),
        "low_v2_card_count": card_by_grade_v2.get("low", 0),
        "pending_card_count": sum(1 for card in cards if card.get("review_status") == "pending"),
        "card_by_type": dict(Counter(card.get("risk_type") or "unknown" for card in cards)),
        "card_by_grade_v2": dict(card_by_grade_v2),
        "max_card_evidence_count": max((len(card.get("evidence") or []) for card in cards), default=0),
    }
