from __future__ import annotations

import uuid
from collections import Counter
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.bidding import BidParseRun, TenderBusinessObject, TenderRequirement, TenderResponseItem, TenderRisk
from app.services.bidding_business_objects import BUSINESS_ACTION_LABELS, BUSINESS_ACTION_OWNER
from app.services.bidding_parser import dumps_json, loads_json


RESPONSE_ACTION_LABELS = {
    "direct_response": "直接响应",
    "qualification_material": "资格材料准备",
    "document_preparation": "文件编制",
    "clarification": "转答疑",
    "quote_allowance": "报价预留",
    "legal_review": "法务复核",
    "reference": "仅参考",
}

RESPONSE_STATUS_LABELS = {
    "pending": "待处理",
    "confirmed": "已确认",
    "to_clarify": "需答疑",
    "to_quote_allowance": "报价预留",
    "legal_review": "法务复核",
    "done": "已完成",
    "ignored": "已忽略",
}

RESPONSE_ACTIONS = set(RESPONSE_ACTION_LABELS)
RESPONSE_STATUSES = set(RESPONSE_STATUS_LABELS)

BUSINESS_ACTION_TO_RESPONSE_ACTION = {
    "bid_compliance": "direct_response",
    "qualification_response": "qualification_material",
    "document_response": "document_preparation",
    "quote_allowance": "quote_allowance",
    "to_quote_allowance": "quote_allowance",
    "clarification": "clarification",
    "to_clarify": "clarification",
    "legal_review": "legal_review",
    "manual_blocking_review": "legal_review",
    "bid_decision_review": "legal_review",
    "delivery_planning": "direct_response",
    "confirmed": "direct_response",
    "reference": "reference",
}

IMPORTANT_UNCOVERED_REQUIREMENT_TYPES = {
    "qualification",
    "technical",
    "commercial",
    "schedule",
    "submission",
    "bill",
    "brand",
}

QUOTE_RISK_TYPES = {
    "fixed_total_price",
    "no_price_adjustment",
    "omission_liability",
    "material_brand_constraint",
    "design_or_drawing_unclear",
}

LEGAL_RISK_TYPES = {
    "advance_funding",
    "delayed_payment",
    "liquidated_damages",
    "claim_time_limit",
    "site_condition",
}

TECHNICAL_REQUIREMENT_CLUSTERS = {
    "construction_plan": {
        "title": "技术标：施工组织设计与管理人员配置",
        "keywords": ("施工组织设计", "组织架构", "项目经理", "管理人员", "临时设施", "周围环境保护"),
        "note": "需在技术标中说明施工组织、项目班子、临设安排、安全文明和环境保护响应。",
    },
    "quality_acceptance": {
        "title": "技术标：质量标准、验收规范与检测要求",
        "keywords": ("质量", "验收标准", "技术规范", "国家", "规范", "标准", "检测", "GB", "空气质量"),
        "note": "需在技术标中承诺按较高标准执行，并列明质量验收、检测和过程控制措施。",
    },
    "handover_acceptance": {
        "title": "技术标：工作面移交与过程验收",
        "keywords": ("移交", "工作面", "验收表", "土建精装", "一米线", "隐蔽验收"),
        "note": "需说明工作面接收、过程验收、隐蔽验收和记录表单的管理方式。",
    },
    "material_control": {
        "title": "技术标：材料品质、环保检测与进场验收",
        "keywords": ("材料", "石材", "瓷砖", "不锈钢", "甲醛", "TVOC", "环保", "进场验收", "材料验收"),
        "note": "需说明材料选型、样板一致性、环保指标、进场报验和复检安排。",
    },
    "finished_product_protection": {
        "title": "技术标：成品保护与交叉施工保护",
        "keywords": ("成品保护", "电梯", "保护膜", "保护到竣工", "修复", "交叉施工"),
        "note": "需说明自身成品保护、他方成品保护、交叉作业保护和损坏修复责任。",
    },
    "construction_process": {
        "title": "技术标：批量装修流程与工厂化加工",
        "keywords": ("施工流程", "工厂化", "集中加工", "作业指引", "关键工序", "节点"),
        "note": "需说明批量装修流程、集中加工、关键工序节点和标准化施工管理。",
    },
    "schedule_control": {
        "title": "技术标：施工进度计划与甲供/甲指材料配合",
        "keywords": ("施工进度", "月进度", "周进度", "加工周期", "进场时间", "进度滞后"),
        "note": "需说明总进度、月周计划、材料加工周期、进场配合和滞后纠偏措施。",
    },
    "safety_civilized": {
        "title": "技术标：安全文明施工与防火临电管理",
        "keywords": ("安全文明", "防火", "临时用电", "乱搭乱接", "专题方案", "施工安全"),
        "note": "需在技术标中设置安全文明、防火、临电和现场管理专项响应。",
    },
}

GENERIC_TECHNICAL_RESPONSE_TITLES = {
    "识别到技术质量要求，请在后续响应矩阵中确认是否满足并绑定证明材料。",
}

RESPONSE_REVIEW_ROLE_LABELS = {
    "business": "经营",
    "budget": "预算",
    "technical": "技术",
    "legal": "法务",
}

RESPONSE_REVIEW_ROLE_ALIASES = {
    "all": None,
    "全部": None,
    "business": "经营",
    "经营": "经营",
    "商务": "经营",
    "投标": "经营",
    "budget": "预算",
    "预算": "预算",
    "造价": "预算",
    "成本": "预算",
    "technical": "技术",
    "技术": "技术",
    "施工": "技术",
    "工程": "技术",
    "legal": "法务",
    "法务": "法务",
    "合同": "法务",
    "合约": "法务",
}

ROLE_OWNER_KEYWORDS = {
    "经营": ("经营", "商务", "投标", "文件"),
    "预算": ("预算", "造价", "成本", "报价"),
    "技术": ("技术", "施工", "工程"),
    "法务": ("法务", "合同", "合约"),
}

QUALITY_AUTO_STATUSES = {"pending", "to_clarify", "to_quote_allowance", "legal_review"}

REVIEW_ACTION_LABELS = {
    "confirm_response": "确认响应",
    "prepare_qualification": "准备资格材料",
    "write_business_document": "写入商务标",
    "write_technical_document": "写入技术标",
    "budget_assessment": "转预算测算",
    "legal_assessment": "转法务判断",
    "clarification_question": "形成答疑问题",
}

REVIEW_ACTION_DONE_CRITERIA = {
    "confirm_response": "已确认是否响应、响应口径和对应投标文件位置。",
    "prepare_qualification": "资质、业绩、人员或证照材料已准备并绑定到投标文件清单。",
    "write_business_document": "已写入商务标/响应表/承诺函，并确认格式和签章要求。",
    "write_technical_document": "已写入技术标方案或专项措施，并保留对应证明材料。",
    "budget_assessment": "预算已确认费用预留、报价口径或是否需要单列说明。",
    "legal_assessment": "法务已确认责任边界、偏离意见、答疑或投标决策建议。",
    "clarification_question": "已判断是否形成标前答疑，并给出问题文本或确认无需答疑。",
}

TASK_DISPLAY_TYPE_LABELS = {
    "single_task": "单项任务",
    "theme_task": "主题任务",
    "summary_task": "汇总任务",
    "group_task": "分组任务",
}

REVIEW_PRIORITY_LABELS = {
    "P0": "第1波-阻断/决策",
    "P1": "第1波-高责任/高金额",
    "P2": "第2波-必须响应",
    "P3": "第3波-常规补齐",
}

REVIEW_WAVE_LABELS = {
    "wave_1": "第1波",
    "wave_2": "第2波",
    "wave_3": "第3波",
}

GROUP_TITLE_PATTERN = re.compile(r"(?:（第(\d+)组）|\(第(\d+)组\))$")

PRIMARY_ROLE_BY_ACTION = {
    "qualification_material": "经营",
    "document_preparation": "经营",
    "direct_response": "经营",
    "clarification": "经营",
    "quote_allowance": "预算",
    "legal_review": "法务",
}

TECHNICAL_RESPONSE_CATEGORIES = {"technical_requirement"}
TECHNICAL_RESPONSE_KEYWORDS = ("技术标", "施工组织", "质量", "验收", "材料品质", "环保", "成品保护", "施工进度", "安全文明")
LEGAL_RESPONSE_KEYWORDS = ("法务", "合同", "责任", "违约", "扣款", "解除", "索赔", "付款", "质保", "偏离")
BUDGET_RESPONSE_KEYWORDS = ("报价", "预算", "费用", "价格", "调价", "总价", "综合单价", "漏项", "暂定", "措施费", "开办费")



def generate_response_matrix_items(db: Session, run: BidParseRun, *, created_by: int) -> dict[str, Any]:
    existing = {
        item.source_key: item
        for item in db.query(TenderResponseItem).filter(TenderResponseItem.parse_run_id == run.id).all()
    }
    business_objects = (
        db.query(TenderBusinessObject)
        .filter(TenderBusinessObject.parse_run_id == run.id, TenderBusinessObject.status == "active")
        .order_by(TenderBusinessObject.id.asc())
        .all()
    )
    requirements = (
        db.query(TenderRequirement)
        .filter(TenderRequirement.parse_run_id == run.id, TenderRequirement.status == "active")
        .order_by(TenderRequirement.id.asc())
        .all()
    )
    risks = db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id).order_by(TenderRisk.id.asc()).all()

    covered_requirement_ids, covered_risk_ids = _covered_source_ids(business_objects)
    uncovered_requirements = [item for item in requirements if item.id not in covered_requirement_ids]
    cluster_candidates, clustered_requirement_ids = _clustered_requirement_candidates(uncovered_requirements)
    superseded_count = _supersede_legacy_technical_requirement_items(existing.values(), clustered_requirement_ids)

    candidates: list[dict[str, Any]] = []
    for item in business_objects:
        candidate = _candidate_from_business_object(item)
        if candidate:
            candidates.append(candidate)
    for risk in risks:
        if risk.id not in covered_risk_ids:
            candidate = _candidate_from_risk(risk)
            if candidate:
                candidates.append(candidate)
    candidates.extend(cluster_candidates)
    for requirement in uncovered_requirements:
        if requirement.id in clustered_requirement_ids:
            continue
        candidate = _candidate_from_requirement(requirement)
        if candidate:
            candidates.append(candidate)

    created_items: list[TenderResponseItem] = []
    skipped_existing = 0
    metadata_updated = 0
    for candidate in candidates:
        if candidate["source_key"] in existing:
            if _backfill_generated_metadata(existing[candidate["source_key"]], candidate):
                metadata_updated += 1
            skipped_existing += 1
            continue
        item = TenderResponseItem(
            response_item_uuid=str(uuid.uuid4()),
            project_id=run.project_id,
            parse_run_id=run.id,
            business_object_id=candidate.get("business_object_id"),
            requirement_id=candidate.get("requirement_id"),
            risk_id=candidate.get("risk_id"),
            source_key=candidate["source_key"],
            response_category=candidate["response_category"],
            response_action=candidate["response_action"],
            response_title=candidate["response_title"][:255],
            source_text=candidate["source_text"],
            evidence_json=dumps_json(candidate.get("evidence") or []),
            owner_role=candidate.get("owner_role"),
            risk_level=candidate.get("risk_level") or "low",
            status=candidate.get("status") or "pending",
            response_note=candidate.get("response_note"),
            reviewer_note=None,
            created_from=candidate.get("created_from") or "business_object",
            normalized_json=dumps_json(candidate.get("normalized") or {}),
            created_by=created_by,
        )
        db.add(item)
        created_items.append(item)
        existing[item.source_key] = item
    db.flush()

    quality_result = _apply_response_matrix_quality_rules(db, run, created_by=created_by)
    db.flush()

    all_items = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    visible_items = [item for item in all_items if not _is_superseded_response_item(item)]
    return {
        "run_uuid": run.run_uuid,
        "candidate_count": len(candidates),
        "created_count": len(created_items),
        "skipped_existing_count": skipped_existing,
        "metadata_updated_count": metadata_updated,
        "superseded_count": superseded_count + quality_result["quality_hidden_count"],
        "quality_merged_count": quality_result["quality_merged_count"],
        "quality_split_parent_count": quality_result["quality_split_parent_count"],
        "quality_split_child_count": quality_result["quality_split_child_count"],
        "quality_restored_count": quality_result.get("quality_restored_count", 0),
        "quality_metadata_updated_count": quality_result.get("quality_metadata_updated_count", 0),
        "total_count": len(visible_items),
        "stored_total_count": len(all_items),
        "summary": build_response_matrix_summary(visible_items),
        "created_item_uuids": [item.response_item_uuid for item in created_items],
    }


def build_response_matrix_summary(items: list[TenderResponseItem | dict[str, Any]]) -> dict[str, Any]:
    by_status: Counter[str] = Counter()
    by_action: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_risk_level: Counter[str] = Counter()
    covered_requirement_ids: set[int] = set()
    covered_risk_ids: set[int] = set()
    workflow_actions: Counter[str] = Counter()
    review_roles: Counter[str] = Counter()
    primary_review_roles: Counter[str] = Counter()
    review_actions: Counter[str] = Counter()
    coverage_classifications: Counter[str] = Counter()
    task_display_types: Counter[str] = Counter()
    review_priorities: Counter[str] = Counter()
    review_waves: Counter[str] = Counter()
    quality_flags: Counter[str] = Counter()
    clustered_requirement_count = 0
    split_item_count = 0
    for item in items:
        by_status[_value(item, "status") or "pending"] += 1
        by_action[_value(item, "response_action") or "direct_response"] += 1
        by_category[_value(item, "response_category") or "unknown"] += 1
        by_risk_level[_value(item, "risk_level") or "low"] += 1
        normalized = _normalized_value(item)
        coverage = normalized.get("coverage") if isinstance(normalized.get("coverage"), dict) else {}
        covered_requirement_ids.update(_int_list(coverage.get("requirement_ids")))
        covered_risk_ids.update(_int_list(coverage.get("risk_ids")))
        if _value(item, "requirement_id"):
            covered_requirement_ids.update(_int_list([_value(item, "requirement_id")]))
        if _value(item, "risk_id"):
            covered_risk_ids.update(_int_list([_value(item, "risk_id")]))
        for action in normalized.get("workflow_actions") if isinstance(normalized.get("workflow_actions"), list) else []:
            if isinstance(action, dict) and action.get("action"):
                workflow_actions[str(action["action"])] += 1
        for role in _response_item_review_roles(item):
            review_roles[role] += 1
        primary_role = _response_item_primary_review_role(item)
        if primary_role:
            primary_review_roles[primary_role] += 1
        review_action = normalized.get("review_action")
        if review_action:
            review_actions[str(review_action)] += 1
        coverage_classification = normalized.get("coverage_classification")
        if coverage_classification:
            coverage_classifications[str(coverage_classification)] += 1
        task_display_type = normalized.get("task_display_type")
        if task_display_type:
            task_display_types[str(task_display_type)] += 1
        review_priority = normalized.get("review_priority")
        if review_priority:
            review_priorities[str(review_priority)] += 1
        review_wave = normalized.get("review_wave")
        if review_wave:
            review_waves[str(review_wave)] += 1
        for flag in normalized.get("quality_flags") if isinstance(normalized.get("quality_flags"), list) else []:
            quality_flags[str(flag)] += 1
        if normalized.get("source") == "requirement_cluster":
            clustered_requirement_count += 1
        if normalized.get("source") == "quality_split" or _value(item, "created_from") == "quality_split":
            split_item_count += 1
    return {
        "item_count": len(items),
        "pending_count": by_status.get("pending", 0),
        "confirmed_count": by_status.get("confirmed", 0),
        "to_clarify_count": by_status.get("to_clarify", 0),
        "to_quote_allowance_count": by_status.get("to_quote_allowance", 0),
        "legal_review_count": by_status.get("legal_review", 0),
        "done_count": by_status.get("done", 0),
        "ignored_count": by_status.get("ignored", 0),
        "high_risk_count": by_risk_level.get("high", 0),
        "by_status": dict(by_status),
        "by_action": dict(by_action),
        "by_category": dict(by_category),
        "by_risk_level": dict(by_risk_level),
        "covered_requirement_count": len(covered_requirement_ids),
        "covered_risk_count": len(covered_risk_ids),
        "clustered_requirement_count": clustered_requirement_count,
        "by_workflow_action": dict(workflow_actions),
        "by_review_role": dict(review_roles),
        "by_primary_review_role": dict(primary_review_roles),
        "by_review_action": dict(review_actions),
        "by_coverage_classification": dict(coverage_classifications),
        "by_task_display_type": dict(task_display_types),
        "by_review_priority": dict(review_priorities),
        "by_review_wave": dict(review_waves),
        "quality_flag_count": sum(quality_flags.values()),
        "by_quality_flag": dict(quality_flags),
        "split_item_count": split_item_count,
        "action_labels": RESPONSE_ACTION_LABELS,
        "status_labels": RESPONSE_STATUS_LABELS,
        "review_role_labels": RESPONSE_REVIEW_ROLE_LABELS,
        "task_display_type_labels": TASK_DISPLAY_TYPE_LABELS,
        "review_priority_labels": REVIEW_PRIORITY_LABELS,
        "review_wave_labels": REVIEW_WAVE_LABELS,
    }


def _candidate_from_business_object(item: TenderBusinessObject) -> dict[str, Any] | None:
    if item.review_status == "ignored" or not item.response_required:
        return None
    normalized = loads_json(item.normalized_json, {}) if item.normalized_json else {}
    llm_status = normalized.get("llm_review_status")
    effective = normalized.get("llm_review_effective") if llm_status in {"accepted", "modified"} else None
    if not isinstance(effective, dict):
        effective = {}
    source_action = (
        effective.get("primary_business_action")
        or normalized.get("primary_business_action")
        or normalized.get("business_action")
        or "direct_response"
    )
    response_action = _response_action_from_business_action(str(source_action))
    response_title = str(effective.get("suggested_title") or item.title or "投标响应项").strip()
    response_note = _first_text(
        effective.get("suggested_reviewer_note"),
        effective.get("reason"),
        normalized.get("business_action_label"),
        BUSINESS_ACTION_LABELS.get(str(source_action)),
    )
    evidence = _compact_evidence(loads_json(item.evidence_json, []) if item.evidence_json else [])
    related_requirement_ids = _int_list(loads_json(item.related_requirement_ids_json, []) if item.related_requirement_ids_json else [])
    related_risk_ids = _int_list(loads_json(item.related_risk_ids_json, []) if item.related_risk_ids_json else [])
    risk_types = _risk_types_from_business_object(item, normalized, evidence)
    workflow_actions = _workflow_actions(
        response_action,
        category=str(effective.get("suggested_object_type") or item.object_type or "business_object"),
        risk_types=risk_types,
        object_subtype=item.object_subtype,
        secondary_business_actions=_secondary_business_actions(normalized, effective),
    )
    coverage = _coverage_payload(
        source_label="业务对象",
        requirement_ids=related_requirement_ids + ([item.requirement_id] if item.requirement_id else []),
        risk_ids=related_risk_ids + ([item.risk_id] if item.risk_id else []),
        evidence=evidence,
        source_count=item.source_count,
        explanation_prefix=f"由业务对象「{item.title}」生成",
    )
    return {
        "source_key": f"bo:{item.object_uuid}",
        "business_object_id": item.id,
        "requirement_id": item.requirement_id,
        "risk_id": item.risk_id,
        "response_category": str(effective.get("suggested_object_type") or item.object_type or "business_object"),
        "response_action": response_action,
        "response_title": response_title,
        "source_text": item.original_text or response_title,
        "evidence": evidence,
        "owner_role": item.owner_role or BUSINESS_ACTION_OWNER.get(str(source_action)) or _owner_from_response_action(response_action),
        "risk_level": _risk_level_from_business_object(item, normalized),
        "status": _initial_status(item.review_status, response_action),
        "response_note": response_note,
        "created_from": "business_object",
        "normalized": {
            "source": "business_object",
            "object_uuid": item.object_uuid,
            "object_type": item.object_type,
            "object_subtype": item.object_subtype,
            "suggested_object_type": effective.get("suggested_object_type"),
            "suggested_object_subtype": effective.get("suggested_object_subtype"),
            "business_action": normalized.get("business_action"),
            "primary_business_action": normalized.get("primary_business_action"),
            "response_business_action": source_action,
            "llm_review_status": llm_status,
            "llm_review_applied": bool(effective),
            "llm_review_rejected": llm_status == "rejected",
            "related_requirement_ids": related_requirement_ids,
            "related_risk_ids": related_risk_ids,
            "risk_types": risk_types,
            "workflow_actions": workflow_actions,
            "coverage": coverage,
            "coverage_explanation": coverage["explanation"],
            "risk_cards": normalized.get("risk_cards") if isinstance(normalized.get("risk_cards"), list) else [],
        },
    }


def _candidate_from_risk(risk: TenderRisk) -> dict[str, Any] | None:
    if risk.review_status == "ignored":
        return None
    if not (risk.is_blocking or risk.risk_level == "high" or risk.review_status in {"to_clarify", "to_quote_allowance"}):
        return None
    response_action = _response_action_from_risk(risk)
    evidence = [
        {
            "source_kind": "risk",
            "risk_uuid": risk.risk_uuid,
            "source_file": risk.source_file,
            "source_location": risk.source_location,
            "original_text": risk.original_text,
            "risk_type": risk.risk_type,
            "risk_level": risk.risk_level,
        }
    ]
    workflow_actions = _workflow_actions(
        response_action,
        category="risk",
        risk_types=[risk.risk_type],
        object_subtype=risk.risk_type,
        secondary_business_actions=[],
    )
    coverage = _coverage_payload(
        source_label="风险明细",
        requirement_ids=[risk.requirement_id] if risk.requirement_id else [],
        risk_ids=[risk.id],
        evidence=evidence,
        source_count=1,
        explanation_prefix="由未被业务对象覆盖的高风险/阻断风险生成",
    )
    return {
        "source_key": f"risk:{risk.risk_uuid}",
        "risk_id": risk.id,
        "requirement_id": risk.requirement_id,
        "response_category": "risk",
        "response_action": response_action,
        "response_title": _first_text(risk.risk_explanation, risk.risk_type, "风险响应项") or "风险响应项",
        "source_text": risk.original_text or risk.risk_explanation,
        "evidence": evidence,
        "owner_role": _owner_from_response_action(response_action),
        "risk_level": risk.risk_level or "medium",
        "status": _initial_status(risk.review_status, response_action),
        "response_note": risk.suggested_action or risk.risk_explanation,
        "created_from": "risk",
        "normalized": {
            "source": "risk",
            "risk_uuid": risk.risk_uuid,
            "risk_type": risk.risk_type,
            "impact_area": risk.impact_area,
            "is_blocking": bool(risk.is_blocking),
            "workflow_actions": workflow_actions,
            "coverage": coverage,
            "coverage_explanation": coverage["explanation"],
        },
    }


def _candidate_from_requirement(requirement: TenderRequirement) -> dict[str, Any] | None:
    if requirement.requirement_type not in IMPORTANT_UNCOVERED_REQUIREMENT_TYPES:
        return None
    response_action = _response_action_from_requirement(requirement)
    status = "confirmed" if requirement.compliance_status == "confirmed" else "pending"
    evidence = [
        {
            "source_kind": "requirement",
            "requirement_uuid": requirement.requirement_uuid,
            "source_file": requirement.source_file,
            "source_location": requirement.source_location,
            "original_text": requirement.original_text,
        }
    ]
    workflow_actions = _workflow_actions(
        response_action,
        category="requirement",
        risk_types=[],
        object_subtype=requirement.requirement_type,
        secondary_business_actions=[],
    )
    coverage = _coverage_payload(
        source_label="招标要求",
        requirement_ids=[requirement.id],
        risk_ids=[],
        evidence=evidence,
        source_count=1,
        explanation_prefix="由未被业务对象覆盖的关键招标要求生成",
    )
    return {
        "source_key": f"req:{requirement.requirement_uuid}",
        "requirement_id": requirement.id,
        "response_category": "requirement",
        "response_action": response_action,
        "response_title": _response_title_from_requirement(requirement),
        "source_text": requirement.original_text or requirement.parsed_requirement,
        "evidence": evidence,
        "owner_role": requirement.owner_role or _owner_from_response_action(response_action),
        "risk_level": requirement.risk_level or "low",
        "status": status,
        "response_note": requirement.output_section,
        "created_from": "requirement",
        "normalized": {
            "source": "requirement",
            "requirement_uuid": requirement.requirement_uuid,
            "requirement_type": requirement.requirement_type,
            "compliance_status": requirement.compliance_status,
            "output_section": requirement.output_section,
            "workflow_actions": workflow_actions,
            "coverage": coverage,
            "coverage_explanation": coverage["explanation"],
        },
    }


def _clustered_requirement_candidates(requirements: list[TenderRequirement]) -> tuple[list[dict[str, Any]], set[int]]:
    technical_requirements = [item for item in requirements if item.requirement_type == "technical"]
    if not technical_requirements:
        return [], set()

    groups: dict[str, list[TenderRequirement]] = {}
    for requirement in technical_requirements:
        cluster_key = _technical_requirement_cluster_key(requirement)
        groups.setdefault(cluster_key, []).append(requirement)

    candidates: list[dict[str, Any]] = []
    clustered_ids: set[int] = set()
    for cluster_key, group in sorted(groups.items(), key=lambda pair: _cluster_sort_key(pair[0], pair[1])):
        config = TECHNICAL_REQUIREMENT_CLUSTERS.get(cluster_key) or {
            "title": "技术标：其他技术质量响应",
            "note": "需在技术标中逐项确认其他技术质量要求并绑定响应措施。",
        }
        evidence = [
            {
                "source_kind": "requirement",
                "requirement_uuid": item.requirement_uuid,
                "source_file": item.source_file,
                "source_location": item.source_location,
                "original_text": item.original_text,
            }
            for item in group[:8]
        ]
        requirement_ids = [item.id for item in group]
        clustered_ids.update(requirement_ids)
        risk_level = _max_risk_level([item.risk_level for item in group])
        workflow_actions = _workflow_actions(
            "document_preparation",
            category="technical_requirement",
            risk_types=[],
            object_subtype=cluster_key,
            secondary_business_actions=[],
        )
        coverage = _coverage_payload(
            source_label="技术要求聚类",
            requirement_ids=requirement_ids,
            risk_ids=[],
            evidence=evidence,
            source_count=len(group),
            explanation_prefix=f"合并 {len(group)} 条同主题技术要求",
        )
        original_snippets = [_short_text(item.original_text or item.parsed_requirement, 120) for item in group[:5]]
        candidates.append(
            {
                "source_key": f"req_cluster:technical:{cluster_key}",
                "requirement_id": group[0].id,
                "response_category": "technical_requirement",
                "response_action": "document_preparation",
                "response_title": str(config["title"]),
                "source_text": "\n".join(f"{index + 1}. {text}" for index, text in enumerate(original_snippets)),
                "evidence": evidence,
                "owner_role": "技术",
                "risk_level": risk_level,
                "status": "pending",
                "response_note": str(config["note"]),
                "created_from": "requirement_cluster",
                "normalized": {
                    "source": "requirement_cluster",
                    "requirement_type": "technical",
                    "cluster_key": cluster_key,
                    "cluster_title": str(config["title"]),
                    "requirement_ids": requirement_ids,
                    "requirement_uuids": [item.requirement_uuid for item in group],
                    "requirement_count": len(group),
                    "workflow_actions": workflow_actions,
                    "coverage": coverage,
                    "coverage_explanation": coverage["explanation"],
                },
            }
        )
    return candidates, clustered_ids


def _supersede_legacy_technical_requirement_items(items: Any, clustered_requirement_ids: set[int]) -> int:
    if not clustered_requirement_ids:
        return 0
    count = 0
    for item in list(items):
        if item.created_from != "requirement":
            continue
        if item.requirement_id not in clustered_requirement_ids:
            continue
        normalized = loads_json(item.normalized_json, {}) if item.normalized_json else {}
        if normalized.get("source") != "requirement" or normalized.get("requirement_type") != "technical":
            continue
        if _is_superseded_response_item(item):
            continue
        if item.reviewed_by or item.reviewer_note:
            continue
        if item.status not in {"pending", "confirmed"}:
            continue
        if item.response_title not in GENERIC_TECHNICAL_RESPONSE_TITLES and item.response_note not in {None, "", "技术标"}:
            continue
        normalized["superseded_by"] = "technical_requirement_cluster"
        normalized["superseded_reason"] = "技术要求已合并为主题聚类响应项"
        item.normalized_json = dumps_json(normalized)
        item.status = "ignored"
        item.response_note = "已合并到技术要求聚类响应项"
        count += 1
    return count


def _backfill_generated_metadata(item: TenderResponseItem, candidate: dict[str, Any]) -> bool:
    candidate_normalized = candidate.get("normalized") if isinstance(candidate.get("normalized"), dict) else {}
    if not candidate_normalized:
        return False
    normalized = loads_json(item.normalized_json, {}) if item.normalized_json else {}
    changed = False
    for key in (
        "workflow_actions",
        "coverage",
        "coverage_explanation",
        "risk_types",
        "related_requirement_ids",
        "related_risk_ids",
    ):
        if key not in candidate_normalized:
            continue
        if normalized.get(key) != candidate_normalized.get(key):
            normalized[key] = candidate_normalized.get(key)
            changed = True
    if changed:
        item.normalized_json = dumps_json(normalized)
    return changed


def _is_superseded_response_item(item: TenderResponseItem | dict[str, Any]) -> bool:
    normalized = _normalized_value(item)
    return bool(normalized.get("superseded_by"))


def is_superseded_response_item(item: TenderResponseItem | dict[str, Any]) -> bool:
    return _is_superseded_response_item(item)


def normalize_response_review_role(role: str | None) -> str | None:
    text = str(role or "").strip()
    if not text:
        return None
    return RESPONSE_REVIEW_ROLE_ALIASES.get(text) or RESPONSE_REVIEW_ROLE_ALIASES.get(text.lower())


def is_valid_response_review_role(role: str | None) -> bool:
    text = str(role or "").strip()
    if not text:
        return True
    if text.lower() == "all":
        return True
    return normalize_response_review_role(text) is not None


def response_item_review_roles(item: TenderResponseItem | dict[str, Any]) -> list[str]:
    return _response_item_review_roles(item)


def response_item_primary_review_role(item: TenderResponseItem | dict[str, Any]) -> str | None:
    return _response_item_primary_review_role(item)


def response_item_supporting_roles(item: TenderResponseItem | dict[str, Any]) -> list[str]:
    return _response_item_supporting_roles(item)


def response_item_matches_review_role(item: TenderResponseItem | dict[str, Any], role: str | None) -> bool:
    normalized_role = normalize_response_review_role(role)
    if not normalized_role:
        return True
    return normalized_role == _response_item_primary_review_role(item)


def _apply_response_matrix_quality_rules(db: Session, run: BidParseRun, *, created_by: int) -> dict[str, int]:
    risks_by_id = {
        item.id: item
        for item in db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id).all()
    }
    requirements_by_id = {
        item.id: item
        for item in db.query(TenderRequirement).filter(TenderRequirement.parse_run_id == run.id).all()
    }
    items = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    restored_secondary_count = _restore_terminal_secondary_split_items(items)
    recursive_cleanup_count = _suppress_recursive_secondary_split_items(items)
    db.flush()
    items = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    merged_count = _merge_duplicate_response_items(items)
    db.flush()
    items = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    split_parent_count, split_child_count = _split_overloaded_response_items(
        db,
        run,
        items,
        created_by=created_by,
        risks_by_id=risks_by_id,
        requirements_by_id=requirements_by_id,
        allow_quality_split=False,
    )
    db.flush()
    items = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    secondary_split_parent_count, secondary_split_child_count = _split_overloaded_response_items(
        db,
        run,
        items,
        created_by=created_by,
        risks_by_id=risks_by_id,
        requirements_by_id=requirements_by_id,
        allow_quality_split=True,
    )
    db.flush()
    items = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    post_split_merged_count = _merge_duplicate_response_items(items)
    db.flush()
    items = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    metadata_updated_count = _enrich_response_item_task_metadata(items)
    total_split_parent_count = split_parent_count + secondary_split_parent_count
    total_split_child_count = split_child_count + secondary_split_child_count
    return {
        "quality_merged_count": merged_count + post_split_merged_count,
        "quality_split_parent_count": total_split_parent_count,
        "quality_split_child_count": total_split_child_count,
        "quality_hidden_count": recursive_cleanup_count + merged_count + total_split_parent_count + post_split_merged_count,
        "quality_restored_count": restored_secondary_count,
        "quality_metadata_updated_count": metadata_updated_count,
    }


def _merge_duplicate_response_items(items: list[TenderResponseItem]) -> int:
    groups: dict[tuple[str, str], list[TenderResponseItem]] = {}
    for item in items:
        if not _can_auto_quality_update(item):
            continue
        key = _response_duplicate_key(item)
        if key:
            groups.setdefault(key, []).append(item)

    merged_count = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        sorted_group = sorted(group, key=lambda current: current.id)
        primary = sorted_group[0]
        duplicates = sorted_group[1:]
        _merge_response_item_metadata(primary, duplicates)
        for duplicate in duplicates:
            if _mark_response_item_superseded(
                duplicate,
                superseded_by=primary.response_item_uuid,
                reason="同标题、同类型、同动作的响应项已合并",
                note=f"已合并到「{primary.response_title}」。",
                flags=["duplicate_merged"],
                extra={"merged_into_source_key": primary.source_key},
            ):
                merged_count += 1
    return merged_count


def _suppress_recursive_secondary_split_items(items: list[TenderResponseItem]) -> int:
    suppressed_count = 0
    for item in items:
        if not _can_auto_quality_update(item):
            continue
        if item.created_from != "quality_split":
            continue
        normalized = _normalized_value(item)
        quality_flags = normalized.get("quality_flags")
        if not isinstance(quality_flags, list):
            quality_flags = []
        if "secondary_split_child" not in quality_flags and normalized.get("granularity_level") != "risk_family":
            continue
        source_key = str(item.source_key or "")
        if not _is_recursive_secondary_split_item(item, normalized):
            continue
        if _mark_response_item_superseded(
            item,
            superseded_by="secondary_split_cleanup",
            reason="递归细分项已回收，保留上一层风险族拆分项",
            note="已回收到上一层风险族拆分项。",
            flags=["recursive_secondary_split_cleanup"],
            extra={"recursive_cleanup_source_key": source_key},
        ):
            suppressed_count += 1
    return suppressed_count


def _restore_terminal_secondary_split_items(items: list[TenderResponseItem]) -> int:
    restored_count = 0
    for item in items:
        if item.created_from != "quality_split":
            continue
        if item.reviewed_by or item.reviewer_note:
            continue
        normalized = _normalized_value(item)
        if normalized.get("granularity_level") != "risk_family":
            continue
        quality_flags = normalized.get("quality_flags")
        if not isinstance(quality_flags, list) or "secondary_split_child" not in quality_flags:
            continue
        if _is_recursive_secondary_split_item(item, normalized):
            continue
        superseded_reason = str(normalized.get("superseded_reason") or "")
        restore_reasons = ("响应项承载过多", "递归细分项已回收")
        if not any(token in superseded_reason for token in restore_reasons):
            continue

        for key in ("superseded_by", "superseded_reason", "recursive_cleanup_source_key"):
            normalized.pop(key, None)
        normalized["quality_flags"] = [
            str(flag)
            for flag in quality_flags
            if str(flag) not in {"overloaded_split_parent", "recursive_secondary_split_cleanup"}
        ]
        normalized["quality_flags"] = _dedupe_strings(normalized["quality_flags"] + ["terminal_secondary_split_restored"])
        primary_role = str(normalized.get("primary_review_role") or item.owner_role or "")
        normalized["quality_explanation"] = f"已恢复为{primary_role or '主责人'}可复核的风险族响应项。"
        item.normalized_json = dumps_json(normalized)
        if item.status == "ignored":
            item.status = _initial_status(None, item.response_action)
        item.response_note = normalized["quality_explanation"]
        restored_count += 1
    return restored_count


def _is_recursive_secondary_split_item(item: TenderResponseItem, normalized: dict[str, Any]) -> bool:
    title_text = " ".join(
        str(value or "")
        for value in (
            item.response_title,
            normalized.get("split_parent_title"),
            normalized.get("quality_explanation"),
        )
    )
    if "细分" in title_text:
        return True
    if _task_group_index(item, normalized) is None:
        return False
    parent_title = _task_group_base_title(str(normalized.get("split_parent_title") or ""))
    current_title = _task_group_base_title(item.response_title)
    if not parent_title or not current_title:
        return False
    return parent_title != current_title


def _split_overloaded_response_items(
    db: Session,
    run: BidParseRun,
    items: list[TenderResponseItem],
    *,
    created_by: int,
    risks_by_id: dict[int, TenderRisk],
    requirements_by_id: dict[int, TenderRequirement],
    allow_quality_split: bool,
) -> tuple[int, int]:
    existing_by_source = {item.source_key: item for item in items}
    split_parent_count = 0
    split_child_count = 0
    for item in items:
        if not _can_auto_quality_update(item):
            continue
        if item.created_from == "quality_split" and not allow_quality_split:
            continue
        normalized = _normalized_value(item)
        if normalized.get("quality_split_applied"):
            continue
        plans = _split_plans_for_response_item(
            item,
            risks_by_id=risks_by_id,
            requirements_by_id=requirements_by_id,
            allow_quality_split=allow_quality_split,
        )
        if not plans:
            continue

        child_source_keys: list[str] = []
        child_titles: list[str] = []
        for plan in plans:
            source_key = _quality_split_source_key(item.source_key, str(plan["key"]))
            child_source_keys.append(source_key)
            child_titles.append(str(plan["title"]))
            child = existing_by_source.get(source_key)
            if child:
                _backfill_quality_split_child(child, item, plan)
                continue
            child = TenderResponseItem(
                response_item_uuid=str(uuid.uuid4()),
                project_id=run.project_id,
                parse_run_id=run.id,
                business_object_id=item.business_object_id,
                requirement_id=item.requirement_id,
                risk_id=item.risk_id,
                source_key=source_key,
                response_category=str(plan["category"]),
                response_action=str(plan["action"]),
                response_title=str(plan["title"])[:255],
                source_text=item.source_text,
                evidence_json=item.evidence_json,
                owner_role=str(plan["owner_role"]),
                risk_level=str(plan["risk_level"]),
                status=str(plan["status"]),
                response_note=str(plan["note"]),
                reviewer_note=None,
                created_from="quality_split",
                normalized_json=dumps_json(_quality_split_child_normalized(item, plan, source_key)),
                created_by=created_by,
            )
            db.add(child)
            existing_by_source[source_key] = child
            split_child_count += 1

        if _mark_response_item_superseded(
            item,
            superseded_by="quality_split",
            reason="响应项承载过多风险、要求或协同动作，已拆分为可分工复核项",
            note=f"已拆分为 {len(child_titles)} 个复核项：{'、'.join(child_titles)}。",
            flags=["overloaded_split_parent"],
            extra={
                "quality_split_applied": True,
                "split_child_source_keys": child_source_keys,
                "split_reason": _overload_reason(item),
            },
        ):
            split_parent_count += 1
    return split_parent_count, split_child_count


def _merge_response_item_metadata(primary: TenderResponseItem, duplicates: list[TenderResponseItem]) -> None:
    normalized = _normalized_value(primary)
    evidence = _merged_evidence([primary, *duplicates])
    if evidence:
        primary.evidence_json = dumps_json(evidence)
    coverage = _quality_coverage_payload(
        [primary, *duplicates],
        evidence_count=len(evidence),
        explanation_prefix=f"合并 {len(duplicates) + 1} 条同类响应项「{primary.response_title}」",
    )
    workflow_actions = _merged_workflow_actions([primary, *duplicates])
    if workflow_actions:
        normalized["workflow_actions"] = workflow_actions
    normalized["coverage"] = coverage
    normalized["coverage_explanation"] = coverage["explanation"]
    normalized["quality_explanation"] = f"已合并 {len(duplicates)} 条同标题/同动作响应项，复核时按一项处理。"
    normalized["merged_source_keys"] = _dedupe_strings([primary.source_key] + [item.source_key for item in duplicates])
    normalized["review_roles"] = _ordered_review_roles(
        role
        for item in [primary, *duplicates]
        for role in _response_item_review_roles(item)
    )
    normalized["quality_flags"] = _add_quality_flags(normalized, ["merged_duplicates"])
    primary.normalized_json = dumps_json(normalized)


def _backfill_quality_split_child(child: TenderResponseItem, parent: TenderResponseItem, plan: dict[str, Any]) -> None:
    if child.reviewed_by or child.reviewer_note or child.status in {"done", "confirmed"}:
        return
    normalized = _quality_split_child_normalized(parent, plan, child.source_key)
    child.response_category = str(plan["category"])
    child.response_action = str(plan["action"])
    child.response_title = str(plan["title"])[:255]
    child.owner_role = str(plan["owner_role"])
    child.risk_level = str(plan["risk_level"])
    child.status = str(plan["status"])
    child.response_note = str(plan["note"])
    child.normalized_json = dumps_json(normalized)


def _quality_split_child_normalized(parent: TenderResponseItem, plan: dict[str, Any], source_key: str) -> dict[str, Any]:
    parent_normalized = _normalized_value(parent)
    parent_coverage = parent_normalized.get("coverage") if isinstance(parent_normalized.get("coverage"), dict) else {}
    requirement_ids = _int_list(plan.get("requirement_ids")) or _int_list(parent_coverage.get("requirement_ids"))
    risk_ids = _int_list(plan.get("risk_ids")) or _int_list(parent_coverage.get("risk_ids"))
    source_count = int(plan.get("source_count") or 0) or len(requirement_ids) + len(risk_ids)
    if not source_count:
        source_count = int(parent_coverage.get("source_count") or 0)
    coverage = {
        "source_label": "响应矩阵质量拆分",
        "requirement_ids": requirement_ids,
        "risk_ids": risk_ids,
        "evidence_count": int(parent_coverage.get("evidence_count") or 0),
        "source_count": source_count,
        "explanation": (
            f"从过载响应项「{parent.response_title}」拆分为「{plan['title']}」，"
            f"{plan['coverage_note']}"
        ),
    }
    action = str(plan["action"])
    owner_role = str(plan["owner_role"])
    return {
        "source": "quality_split",
        "split_key": str(plan["key"]),
        "split_parent_uuid": parent.response_item_uuid,
        "split_parent_source_key": parent.source_key,
        "split_parent_title": parent.response_title,
        "source_key": source_key,
        "split_reason": _overload_reason(parent),
        "workflow_actions": [
            {
                "action": action,
                "label": RESPONSE_ACTION_LABELS.get(action, action),
                "owner_role": owner_role,
                "reason": str(plan["reason"]),
            }
        ],
        "coverage": coverage,
        "coverage_explanation": coverage["explanation"],
        "quality_flags": _dedupe_strings(["quality_split_child"] + ([str(plan.get("quality_flag"))] if plan.get("quality_flag") else [])),
        "quality_explanation": f"从「{parent.response_title}」拆分，便于{owner_role}单独复核。",
        "review_roles": _ordered_review_roles([owner_role, _owner_from_response_action(action)]),
        "primary_review_role": owner_role,
        "supporting_roles": _ordered_review_roles([_owner_from_response_action(action)]),
        "granularity_level": str(plan.get("granularity_level") or "role_action"),
        "coverage_classification": str(plan.get("coverage_classification") or "must_respond"),
        "parent_workflow_actions": _response_item_workflow_action_names(parent),
        "parent_coverage": {
            "requirement_count": len(coverage["requirement_ids"]),
            "risk_count": len(coverage["risk_ids"]),
            "source_count": coverage["source_count"],
        },
    }


def _split_plans_for_response_item(
    item: TenderResponseItem,
    *,
    risks_by_id: dict[int, TenderRisk],
    requirements_by_id: dict[int, TenderRequirement],
    allow_quality_split: bool,
) -> list[dict[str, Any]]:
    if item.created_from == "requirement_cluster" or item.response_category == "technical_requirement":
        return []
    normalized = _normalized_value(item)
    if normalized.get("granularity_level") in {"atomic", "risk_family"}:
        return []
    if not _is_overloaded_response_item(item):
        return []
    if item.created_from == "quality_split" or normalized.get("source") == "quality_split":
        if not allow_quality_split:
            return []
        return _secondary_split_plans_for_response_item(item, risks_by_id=risks_by_id, requirements_by_id=requirements_by_id)
    text = _response_item_search_text(item)
    if any(token in text for token in ("漏项", "表格", "报价表", "清单填报", "响应表")):
        return [
            _split_plan(
                "omission_quote",
                "漏项责任报价预留",
                "pricing_constraint",
                "quote_allowance",
                "预算",
                "high",
                "to_quote_allowance",
                "复核清单漏项、表格漏填、报价口径遗漏是否需要预留费用。",
                "聚焦漏项责任和报价预留。",
                "预算需要单独判断清单漏项和表格漏填的报价预留口径。",
            ),
            _split_plan(
                "omission_legal",
                "漏项责任法务复核",
                "contract_clause",
                "legal_review",
                "法务",
                "high",
                "legal_review",
                "复核漏项责任转嫁、重大偏差或不响应后果是否需要保留偏离意见。",
                "聚焦合同责任和废标/重大偏差风险。",
                "法务需要单独复核漏项责任、重大偏差和不响应后果。",
            ),
            _split_plan(
                "form_filling",
                "清单/表格填报响应要求",
                "document_checklist",
                "document_preparation",
                "经营",
                item.risk_level or "medium",
                "pending",
                "检查报价表、响应表、承诺表等投标文件是否逐项填报并保持格式一致。",
                "聚焦投标文件填报动作。",
                "经营需要把填表要求落到投标文件清单和编制责任。",
            ),
        ]
    if any(token in text for token in ("违约金", "违约", "扣款", "处罚", "罚款")):
        return [
            _split_plan(
                "default_quote",
                "违约/扣款责任报价预留",
                "pricing_constraint",
                "quote_allowance",
                "预算",
                "high",
                "to_quote_allowance",
                "测算工期、质量、人员、材料等违约或扣款条款的费用预留。",
                "聚焦违约扣款的成本暴露。",
                "预算需要把违约责任转成风险预留或报价说明。",
            ),
            _split_plan(
                "default_legal",
                "违约/扣款责任法务复核",
                "contract_clause",
                "legal_review",
                "法务",
                "high",
                "legal_review",
                "复核违约金上限、扣款触发条件、解除/停工责任和可谈判空间。",
                "聚焦合同责任边界。",
                "法务需要判断违约责任是否需要偏离、澄清或投标决策提示。",
            ),
        ]
    if any(token in text for token in ("不调价", "价格波动", "调价", "综合单价", "固定单价", "固定总价")):
        return [
            _split_plan(
                "price_quote",
                "价格不调整报价预留",
                "pricing_constraint",
                "quote_allowance",
                "预算",
                "high",
                "to_quote_allowance",
                "测算人工、材料、措施费或总价包干条件下的价格波动预留。",
                "聚焦报价测算。",
                "预算需要单独形成价格风险预留口径。",
            ),
            _split_plan(
                "price_legal",
                "价格不调整条款法务复核",
                "contract_clause",
                "legal_review",
                "法务",
                "high",
                "legal_review",
                "复核价格不调整、包干范围、暂定量和变更调价边界。",
                "聚焦合同价款边界。",
                "法务需要判断价格不调整条款是否需要偏离或答疑。",
            ),
        ]

    actions = _response_item_workflow_action_names(item)
    if len(actions) < 4:
        return []
    plans: list[dict[str, Any]] = []
    for action in [value for value in ("quote_allowance", "legal_review", "clarification", "document_preparation") if value in actions]:
        owner = _owner_from_response_action(action)
        plans.append(
            _split_plan(
                action,
                f"{_short_text(item.response_title, 80)} - {RESPONSE_ACTION_LABELS.get(action, action)}",
                item.response_category,
                action,
                owner,
                item.risk_level or "medium",
                _initial_status(None, action),
                f"从过载响应项中拆出 {RESPONSE_ACTION_LABELS.get(action, action)} 动作，便于{owner}复核。",
                f"聚焦 {RESPONSE_ACTION_LABELS.get(action, action)}。",
                f"{owner}需要单独处理该协同动作。",
            )
        )
        if len(plans) >= 3:
            break
    return plans


def _split_plan(
    key: str,
    title: str,
    category: str,
    action: str,
    owner_role: str,
    risk_level: str,
    status: str,
    note: str,
    coverage_note: str,
    reason: str,
    requirement_ids: list[int] | None = None,
    risk_ids: list[int] | None = None,
    granularity_level: str = "role_action",
    coverage_classification: str = "must_respond",
    quality_flag: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "category": category,
        "action": action,
        "owner_role": owner_role,
        "risk_level": risk_level,
        "status": status,
        "note": note,
        "coverage_note": coverage_note,
        "reason": reason,
        "requirement_ids": requirement_ids or [],
        "risk_ids": risk_ids or [],
        "source_count": len(requirement_ids or []) + len(risk_ids or []),
        "granularity_level": granularity_level,
        "coverage_classification": coverage_classification,
        "quality_flag": quality_flag or "",
    }


def _secondary_split_plans_for_response_item(
    item: TenderResponseItem,
    *,
    risks_by_id: dict[int, TenderRisk],
    requirements_by_id: dict[int, TenderRequirement],
) -> list[dict[str, Any]]:
    normalized = _normalized_value(item)
    coverage = _response_item_coverage(item)
    risk_ids = _int_list(coverage.get("risk_ids"))
    requirement_ids = _int_list(coverage.get("requirement_ids"))
    if len(risk_ids) <= 8 and len(requirement_ids) <= 15:
        return []
    text = _response_item_search_text(item)
    action = item.response_action or str(normalized.get("response_action") or "")
    if any(token in text for token in ("漏项", "表格", "清单", "重大偏差", "漏报")):
        if action == "quote_allowance":
            return _build_family_split_plans(
                item,
                families=[
                    {
                        "key": "bill_omission",
                        "title": "清单漏项报价预留",
                        "category": "pricing_constraint",
                        "action": "quote_allowance",
                        "owner_role": "预算",
                        "note": "复核工程量清单漏项、缺项、项目特征缺失带来的报价预留。",
                        "coverage_note": "聚焦清单漏项和缺项风险。",
                        "reason": "预算需单独测算清单漏项的费用兜底口径。",
                        "keywords": ("漏项", "缺项", "清单", "工程量", "项目特征", "omission"),
                    },
                    {
                        "key": "form_missing_quote",
                        "title": "表格漏填报价影响复核",
                        "category": "pricing_constraint",
                        "action": "quote_allowance",
                        "owner_role": "预算",
                        "note": "复核报价表、材料表、响应表漏填漏报是否影响报价完整性。",
                        "coverage_note": "聚焦表格漏填漏报对报价的影响。",
                        "reason": "预算需确认漏填漏报是否形成报价风险。",
                        "keywords": ("表格", "报价表", "材料表", "响应表", "漏填", "漏报"),
                    },
                ],
                fallback_max=2,
                risks_by_id=risks_by_id,
                requirements_by_id=requirements_by_id,
            )
        if action == "legal_review":
            return _build_family_split_plans(
                item,
                families=[
                    {
                        "key": "omission_liability_legal",
                        "title": "漏项责任转嫁法务复核",
                        "category": "contract_clause",
                        "action": "legal_review",
                        "owner_role": "法务",
                        "note": "复核漏项、缺项和清单错误责任是否被转嫁给投标人。",
                        "coverage_note": "聚焦漏项责任转嫁。",
                        "reason": "法务需判断漏项责任是否需要偏离、澄清或风险提示。",
                        "keywords": ("漏项", "缺项", "清单", "责任", "承担", "omission"),
                    },
                    {
                        "key": "major_deviation_legal",
                        "title": "未响应重大偏差法务复核",
                        "category": "contract_clause",
                        "action": "legal_review",
                        "owner_role": "法务",
                        "note": "复核漏填、不响应、重大偏差或废标后果。",
                        "coverage_note": "聚焦重大偏差、废标和不响应后果。",
                        "reason": "法务需判断是否形成投标合规或偏离风险。",
                        "keywords": ("重大偏差", "废标", "否决", "无效", "不响应", "漏填", "漏报"),
                    },
                ],
                fallback_max=2,
                risks_by_id=risks_by_id,
                requirements_by_id=requirements_by_id,
            )
        if action == "document_preparation":
            return _build_family_split_plans(
                item,
                families=[
                    {
                        "key": "bill_form_check",
                        "title": "清单表格逐项填报检查",
                        "category": "document_checklist",
                        "action": "document_preparation",
                        "owner_role": "经营",
                        "note": "检查清单、报价表和响应表是否逐项填报、无漏填漏报。",
                        "coverage_note": "聚焦投标文件填报完整性。",
                        "reason": "经营需把填报要求落到文件编制清单。",
                        "keywords": ("清单", "报价表", "响应表", "表格", "填报", "漏填", "漏报"),
                    },
                    {
                        "key": "deviation_table_check",
                        "title": "响应/偏离表一致性检查",
                        "category": "document_checklist",
                        "action": "document_preparation",
                        "owner_role": "经营",
                        "note": "检查响应表、偏离表与招标条款、报价口径是否一致。",
                        "coverage_note": "聚焦响应表和偏离表一致性。",
                        "reason": "经营需确认响应文件一致性，避免形式风险。",
                        "keywords": ("响应", "偏离", "承诺", "一致", "格式", "签章"),
                    },
                ],
                fallback_max=2,
                risks_by_id=risks_by_id,
                requirements_by_id=requirements_by_id,
            )
    if any(token in text for token in ("违约", "扣款", "处罚", "罚款")):
        return _build_family_split_plans(
            item,
            families=[
                {
                    "key": "schedule_penalty",
                    "title": "工期违约责任复核" if action == "legal_review" else "工期违约报价预留",
                    "category": "contract_clause" if action == "legal_review" else "pricing_constraint",
                    "action": action,
                    "owner_role": "法务" if action == "legal_review" else "预算",
                    "note": "复核工期、节点、延期、赶工相关违约责任。",
                    "coverage_note": "聚焦工期违约。",
                    "reason": "工期违约需要单独形成责任判断或费用预留。",
                    "keywords": ("工期", "延期", "逾期", "节点", "进度", "赶工", "delay", "schedule"),
                },
                {
                    "key": "quality_penalty",
                    "title": "质量/验收违约责任复核" if action == "legal_review" else "质量/验收违约报价预留",
                    "category": "contract_clause" if action == "legal_review" else "pricing_constraint",
                    "action": action,
                    "owner_role": "法务" if action == "legal_review" else "预算",
                    "note": "复核质量、验收、整改、维修相关违约或扣款责任。",
                    "coverage_note": "聚焦质量和验收违约。",
                    "reason": "质量验收违约需要单独判断责任边界或预留费用。",
                    "keywords": ("质量", "验收", "整改", "维修", "缺陷", "保修", "quality", "acceptance"),
                },
                {
                    "key": "staff_material_penalty",
                    "title": "人员/材料违约责任复核" if action == "legal_review" else "人员/材料违约报价预留",
                    "category": "contract_clause" if action == "legal_review" else "pricing_constraint",
                    "action": action,
                    "owner_role": "法务" if action == "legal_review" else "预算",
                    "note": "复核项目人员到岗、更换、材料品牌、样板相关违约责任。",
                    "coverage_note": "聚焦人员和材料违约。",
                    "reason": "人员材料违约需要单独复核责任和成本暴露。",
                    "keywords": ("人员", "项目经理", "到岗", "更换", "材料", "品牌", "样板", "material", "staff"),
                },
                {
                    "key": "termination_penalty",
                    "title": "解除/停工责任复核" if action == "legal_review" else "解除/停工责任报价预留",
                    "category": "contract_clause" if action == "legal_review" else "pricing_constraint",
                    "action": action,
                    "owner_role": "法务" if action == "legal_review" else "预算",
                    "note": "复核解除合同、停工、退场、重新发包相关责任。",
                    "coverage_note": "聚焦解除、停工和退场责任。",
                    "reason": "解除停工责任需要单独判断重大合同风险。",
                    "keywords": ("解除", "停工", "退场", "终止", "重新发包", "termination"),
                },
            ],
            fallback_max=4,
            risks_by_id=risks_by_id,
            requirements_by_id=requirements_by_id,
        )
    if any(token in text for token in ("不调整", "不调价", "价格", "综合单价", "固定总价", "包干")):
        return _build_family_split_plans(
            item,
            families=[
                {
                    "key": "labor_material_price",
                    "title": "人工材料价格波动复核" if action == "legal_review" else "人工材料价格波动报价预留",
                    "category": "contract_clause" if action == "legal_review" else "pricing_constraint",
                    "action": action,
                    "owner_role": "法务" if action == "legal_review" else "预算",
                    "note": "复核人工、材料、市场价格波动不调整带来的责任或报价预留。",
                    "coverage_note": "聚焦人工材料价格波动。",
                    "reason": "人工材料波动需要单独形成报价预留或合同判断。",
                    "keywords": ("人工", "材料", "价格波动", "市场", "调价", "no_price_adjustment"),
                },
                {
                    "key": "unit_total_price",
                    "title": "综合单价/总价包干边界复核" if action == "legal_review" else "综合单价/总价包干报价预留",
                    "category": "contract_clause" if action == "legal_review" else "pricing_constraint",
                    "action": action,
                    "owner_role": "法务" if action == "legal_review" else "预算",
                    "note": "复核综合单价、固定总价、包干范围和调价边界。",
                    "coverage_note": "聚焦综合单价与总价包干边界。",
                    "reason": "包干边界需要单独确认报价和合同责任。",
                    "keywords": ("综合单价", "固定总价", "总价", "包干", "fixed_total_price"),
                },
                {
                    "key": "measure_fee_price",
                    "title": "措施费/开办费包干复核" if action == "legal_review" else "措施费/开办费报价预留",
                    "category": "contract_clause" if action == "legal_review" else "pricing_constraint",
                    "action": action,
                    "owner_role": "法务" if action == "legal_review" else "预算",
                    "note": "复核措施费、开办费、临设、赶工等包干费用。",
                    "coverage_note": "聚焦措施费和开办费。",
                    "reason": "措施费开办费需要单独确认报价兜底。",
                    "keywords": ("措施费", "开办费", "临设", "赶工", "措施", "开办"),
                },
            ],
            fallback_max=3,
            risks_by_id=risks_by_id,
            requirements_by_id=requirements_by_id,
        )
    return []


def _build_family_split_plans(
    item: TenderResponseItem,
    *,
    families: list[dict[str, Any]],
    fallback_max: int,
    risks_by_id: dict[int, TenderRisk],
    requirements_by_id: dict[int, TenderRequirement],
) -> list[dict[str, Any]]:
    coverage = _response_item_coverage(item)
    risk_ids = _int_list(coverage.get("risk_ids"))
    requirement_ids = _int_list(coverage.get("requirement_ids"))
    plans: list[dict[str, Any]] = []
    used_risk_ids: set[int] = set()
    used_requirement_ids: set[int] = set()
    for family in families:
        family_risk_ids = _matching_risk_ids(risk_ids, risks_by_id, family["keywords"])
        family_requirement_ids = _matching_requirement_ids(requirement_ids, requirements_by_id, family["keywords"])
        if not family_risk_ids and not family_requirement_ids:
            continue
        used_risk_ids.update(family_risk_ids)
        used_requirement_ids.update(family_requirement_ids)
        plan = _split_plan(
            str(family["key"]),
            str(family["title"]),
            str(family["category"]),
            str(family["action"]),
            str(family["owner_role"]),
            item.risk_level or "high",
            _initial_status(None, str(family["action"])),
            str(family["note"]),
            str(family["coverage_note"]),
            str(family["reason"]),
            requirement_ids=family_requirement_ids,
            risk_ids=family_risk_ids,
            granularity_level="risk_family",
            quality_flag="secondary_split_child",
        )
        plan["parent_title"] = item.response_title
        plans.append(plan)

    remaining_risk_ids = [item_id for item_id in risk_ids if item_id not in used_risk_ids]
    remaining_requirement_ids = [item_id for item_id in requirement_ids if item_id not in used_requirement_ids]
    if remaining_risk_ids or remaining_requirement_ids:
        residual_family = families[min(len(plans), len(families) - 1)]
        plan = _split_plan(
            f"{residual_family['key']}_other",
            f"其他{residual_family['title']}",
            str(residual_family["category"]),
            str(residual_family["action"]),
            str(residual_family["owner_role"]),
            item.risk_level or "medium",
            _initial_status(None, str(residual_family["action"])),
            f"复核未能自动归入细分主题的剩余条款：{item.response_title}。",
            "聚焦剩余未归类条款。",
            "保留剩余证据，避免二次拆分造成漏项。",
            requirement_ids=remaining_requirement_ids,
            risk_ids=remaining_risk_ids,
            granularity_level="risk_family",
            quality_flag="secondary_split_child",
        )
        plan["parent_title"] = item.response_title
        plans.append(plan)

    if len(plans) < 2 and (len(risk_ids) >= 8 or len(requirement_ids) >= 15):
        return _fallback_even_split_plans(item, families=families[:fallback_max], risk_ids=risk_ids, requirement_ids=requirement_ids)
    expanded_plans: list[dict[str, Any]] = []
    for plan in plans:
        expanded_plans.extend(_split_large_family_plan(plan))
    return expanded_plans


def _split_large_family_plan(plan: dict[str, Any], *, max_risks: int = 8, max_requirements: int = 15) -> list[dict[str, Any]]:
    risk_ids = _int_list(plan.get("risk_ids"))
    requirement_ids = _int_list(plan.get("requirement_ids"))
    group_count = max(
        1,
        (len(risk_ids) + max_risks - 1) // max_risks if risk_ids else 1,
        (len(requirement_ids) + max_requirements - 1) // max_requirements if requirement_ids else 1,
    )
    if group_count <= 1:
        return [plan]
    risk_chunks = _chunk_list(risk_ids, group_count)
    requirement_chunks = _chunk_list(requirement_ids, group_count)
    result: list[dict[str, Any]] = []
    base_title = str(plan["title"])
    parent_title = str(plan.get("parent_title") or "")
    if parent_title and (base_title in parent_title or "第" in parent_title):
        base_title = parent_title
    suffix_label = "细分" if "第" in base_title else "第"
    for index in range(group_count):
        chunk_risk_ids = risk_chunks[index] if index < len(risk_chunks) else []
        chunk_requirement_ids = requirement_chunks[index] if index < len(requirement_chunks) else []
        if not chunk_risk_ids and not chunk_requirement_ids:
            continue
        next_plan = dict(plan)
        next_plan["key"] = f"{plan['key']}_g{index + 1}"
        next_plan["title"] = f"{base_title}（{suffix_label}{index + 1}组）"
        next_plan["requirement_ids"] = chunk_requirement_ids
        next_plan["risk_ids"] = chunk_risk_ids
        next_plan["source_count"] = len(chunk_requirement_ids) + len(chunk_risk_ids)
        next_plan["coverage_note"] = f"{plan['coverage_note']}第 {index + 1}/{group_count} 组。"
        result.append(next_plan)
    return result


def _fallback_even_split_plans(
    item: TenderResponseItem,
    *,
    families: list[dict[str, Any]],
    risk_ids: list[int],
    requirement_ids: list[int],
) -> list[dict[str, Any]]:
    if len(families) < 2:
        return []
    plans: list[dict[str, Any]] = []
    risk_chunks = _chunk_list(risk_ids, len(families))
    requirement_chunks = _chunk_list(requirement_ids, len(families))
    for index, family in enumerate(families):
        chunk_risk_ids = risk_chunks[index] if index < len(risk_chunks) else []
        chunk_requirement_ids = requirement_chunks[index] if index < len(requirement_chunks) else []
        if not chunk_risk_ids and not chunk_requirement_ids:
            continue
        plans.append(
            _split_plan(
                str(family["key"]),
                str(family["title"]),
                str(family["category"]),
                str(family["action"]),
                str(family["owner_role"]),
                item.risk_level or "medium",
                _initial_status(None, str(family["action"])),
                str(family["note"]),
                str(family["coverage_note"]),
                str(family["reason"]),
                requirement_ids=chunk_requirement_ids,
                risk_ids=chunk_risk_ids,
                granularity_level="risk_family",
                quality_flag="secondary_split_child",
            )
        )
    return plans


def _matching_risk_ids(risk_ids: list[int], risks_by_id: dict[int, TenderRisk], keywords: Any) -> list[int]:
    return [
        item_id
        for item_id in risk_ids
        if _keyword_matches(
            " ".join(
                str(value or "")
                for value in (
                    risks_by_id.get(item_id).risk_type if risks_by_id.get(item_id) else "",
                    risks_by_id.get(item_id).risk_explanation if risks_by_id.get(item_id) else "",
                    risks_by_id.get(item_id).original_text if risks_by_id.get(item_id) else "",
                    risks_by_id.get(item_id).impact_area if risks_by_id.get(item_id) else "",
                )
            ),
            keywords,
        )
    ]


def _matching_requirement_ids(
    requirement_ids: list[int],
    requirements_by_id: dict[int, TenderRequirement],
    keywords: Any,
) -> list[int]:
    return [
        item_id
        for item_id in requirement_ids
        if _keyword_matches(
            " ".join(
                str(value or "")
                for value in (
                    requirements_by_id.get(item_id).requirement_type if requirements_by_id.get(item_id) else "",
                    requirements_by_id.get(item_id).parsed_requirement if requirements_by_id.get(item_id) else "",
                    requirements_by_id.get(item_id).original_text if requirements_by_id.get(item_id) else "",
                    requirements_by_id.get(item_id).output_section if requirements_by_id.get(item_id) else "",
                )
            ),
            keywords,
        )
    ]


def _keyword_matches(text: str, keywords: Any) -> bool:
    normalized_text = str(text or "").lower()
    return any(str(keyword or "").lower() in normalized_text for keyword in keywords or [])


def _chunk_list(values: list[int], count: int) -> list[list[int]]:
    if count <= 0:
        return []
    chunks: list[list[int]] = [[] for _ in range(count)]
    for index, value in enumerate(values):
        chunks[index % count].append(value)
    return chunks


def _is_overloaded_response_item(item: TenderResponseItem) -> bool:
    normalized = _normalized_value(item)
    if normalized.get("granularity_level") in {"atomic", "risk_family"}:
        return False
    coverage = _response_item_coverage(item)
    action_count = len(_response_item_workflow_action_names(item))
    risk_count = len(_int_list(coverage.get("risk_ids")))
    requirement_count = len(_int_list(coverage.get("requirement_ids")))
    source_count = int(coverage.get("source_count") or 0)
    text = _response_item_search_text(item)
    if action_count >= 4:
        return True
    if risk_count > 8 or requirement_count > 15 or source_count > 20:
        return True
    return any(token in text for token in ("集合", "条款集合", "漏项", "表格")) and (risk_count + requirement_count) >= 5


def _overload_reason(item: TenderResponseItem) -> str:
    coverage = _response_item_coverage(item)
    action_count = len(_response_item_workflow_action_names(item))
    risk_count = len(_int_list(coverage.get("risk_ids")))
    requirement_count = len(_int_list(coverage.get("requirement_ids")))
    source_count = int(coverage.get("source_count") or 0)
    parts = []
    if action_count >= 4:
        parts.append(f"{action_count} 个协同动作")
    if risk_count:
        parts.append(f"{risk_count} 条风险")
    if requirement_count:
        parts.append(f"{requirement_count} 条要求")
    if source_count:
        parts.append(f"{source_count} 条来源样本")
    return "、".join(parts) or "响应项过载"


def _can_auto_quality_update(item: TenderResponseItem) -> bool:
    if _is_superseded_response_item(item):
        return False
    if item.reviewed_by or item.reviewer_note:
        return False
    return (item.status or "pending") in QUALITY_AUTO_STATUSES


def _response_duplicate_key(item: TenderResponseItem) -> tuple[str, str] | None:
    title_key = _canonical_response_text(item.response_title)
    if len(title_key) < 4:
        return None
    return (
        title_key,
        str(item.response_category or ""),
    )


def _canonical_response_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    for token in (" ", "\n", "\t", "\r", "：", ":", "，", ",", "。", ".", "、", "/", "\\", "-", "_", "—", "(", ")", "（", "）", "《", "》", "“", "”", "\"", "'"):
        text = text.replace(token, "")
    return text


def _response_item_search_text(item: TenderResponseItem) -> str:
    normalized = _normalized_value(item)
    values = [
        item.response_title,
        item.response_note,
        item.source_text,
        item.response_category,
        item.response_action,
        normalized.get("object_type"),
        normalized.get("object_subtype"),
        normalized.get("risk_type"),
        " ".join(str(value) for value in normalized.get("risk_types", []) if value)
        if isinstance(normalized.get("risk_types"), list)
        else "",
    ]
    return " ".join(str(value or "") for value in values).lower()


def _quality_split_source_key(parent_source_key: str, split_key: str) -> str:
    raw = f"quality_split:{parent_source_key}:{split_key}"
    if len(raw) <= 128:
        return raw
    return f"quality_split:{uuid.uuid5(uuid.NAMESPACE_URL, raw)}:{split_key[:24]}"


def _mark_response_item_superseded(
    item: TenderResponseItem,
    *,
    superseded_by: str,
    reason: str,
    note: str,
    flags: list[str],
    extra: dict[str, Any] | None = None,
) -> bool:
    if _is_superseded_response_item(item):
        return False
    normalized = _normalized_value(item)
    normalized["superseded_by"] = superseded_by
    normalized["superseded_reason"] = reason
    normalized["quality_explanation"] = note
    normalized["quality_flags"] = _add_quality_flags(normalized, flags)
    if extra:
        normalized.update(extra)
    item.normalized_json = dumps_json(normalized)
    item.status = "ignored"
    item.response_note = note
    return True


def _response_item_coverage(item: TenderResponseItem | dict[str, Any]) -> dict[str, Any]:
    normalized = _normalized_value(item)
    coverage = normalized.get("coverage") if isinstance(normalized.get("coverage"), dict) else {}
    return coverage


def _quality_coverage_payload(
    items: list[TenderResponseItem],
    *,
    evidence_count: int,
    explanation_prefix: str,
) -> dict[str, Any]:
    requirement_ids: list[int] = []
    risk_ids: list[int] = []
    source_count = 0
    for item in items:
        coverage = _response_item_coverage(item)
        requirement_ids.extend(_int_list(coverage.get("requirement_ids")))
        risk_ids.extend(_int_list(coverage.get("risk_ids")))
        if item.requirement_id:
            requirement_ids.append(item.requirement_id)
        if item.risk_id:
            risk_ids.append(item.risk_id)
        try:
            source_count += int(coverage.get("source_count") or 0)
        except (TypeError, ValueError):
            source_count += 0
    requirement_ids = _int_list(requirement_ids)
    risk_ids = _int_list(risk_ids)
    source_count = source_count or len(items)
    parts = [explanation_prefix]
    if requirement_ids:
        parts.append(f"覆盖 {len(requirement_ids)} 条要求")
    if risk_ids:
        parts.append(f"覆盖 {len(risk_ids)} 条风险")
    parts.append(f"保留 {evidence_count} 条代表证据")
    return {
        "source_label": "响应矩阵质量合并",
        "requirement_ids": requirement_ids,
        "risk_ids": risk_ids,
        "evidence_count": evidence_count,
        "source_count": source_count,
        "explanation": "，".join(parts) + "。",
    }


def _merged_evidence(items: list[TenderResponseItem], limit: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        evidence = loads_json(item.evidence_json, []) if item.evidence_json else []
        for entry in evidence:
            if not isinstance(entry, dict):
                continue
            key = (
                str(entry.get("source_file") or ""),
                str(entry.get("source_location") or ""),
                str(entry.get("original_text") or entry.get("text") or "")[:120],
            )
            if key in seen:
                continue
            result.append(entry)
            seen.add(key)
            if len(result) >= limit:
                return result
    return result


def _add_quality_flags(normalized: dict[str, Any], flags: list[str]) -> list[str]:
    existing = normalized.get("quality_flags") if isinstance(normalized.get("quality_flags"), list) else []
    return _dedupe_strings([str(flag) for flag in existing] + flags)


def _merged_workflow_actions(items: list[TenderResponseItem]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        normalized = _normalized_value(item)
        candidates = normalized.get("workflow_actions") if isinstance(normalized.get("workflow_actions"), list) else []
        if item.response_action:
            candidates = [
                {
                    "action": item.response_action,
                    "label": RESPONSE_ACTION_LABELS.get(item.response_action, item.response_action),
                    "owner_role": _owner_from_response_action(item.response_action),
                    "reason": "主响应动作",
                },
                *candidates,
            ]
        for candidate in candidates:
            if not isinstance(candidate, dict) or not candidate.get("action"):
                continue
            action = str(candidate["action"])
            if action in seen or action not in RESPONSE_ACTIONS or action == "reference":
                continue
            result.append(
                {
                    "action": action,
                    "label": str(candidate.get("label") or RESPONSE_ACTION_LABELS.get(action, action)),
                    "owner_role": str(candidate.get("owner_role") or _owner_from_response_action(action)),
                    "reason": str(candidate.get("reason") or "合并响应项保留的协同动作"),
                }
            )
            seen.add(action)
    return result


def _response_item_workflow_action_names(item: TenderResponseItem | dict[str, Any]) -> list[str]:
    normalized = _normalized_value(item)
    actions = normalized.get("workflow_actions") if isinstance(normalized.get("workflow_actions"), list) else []
    result = [str(action.get("action")) for action in actions if isinstance(action, dict) and action.get("action")]
    primary_action = _value(item, "response_action")
    if primary_action:
        result.insert(0, str(primary_action))
    return [action for action in _dedupe_strings(result) if action in RESPONSE_ACTIONS and action != "reference"]


def _response_item_review_roles(item: TenderResponseItem | dict[str, Any]) -> list[str]:
    normalized = _normalized_value(item)
    roles: list[str] = []
    if isinstance(normalized.get("review_roles"), list):
        for role in normalized["review_roles"]:
            roles.extend(_review_roles_from_text(str(role)))
    roles.extend(_review_roles_from_text(_value(item, "owner_role")))
    for action in _response_item_workflow_action_names(item):
        roles.extend(_review_roles_from_text(_owner_from_response_action(action)))
    workflow_actions = normalized.get("workflow_actions") if isinstance(normalized.get("workflow_actions"), list) else []
    for action in workflow_actions:
        if isinstance(action, dict):
            roles.extend(_review_roles_from_text(action.get("owner_role")))
    category = str(_value(item, "response_category") or "")
    if category == "technical_requirement":
        roles.append("技术")
    if _value(item, "status") == "legal_review":
        roles.append("法务")
    if _value(item, "status") == "to_quote_allowance":
        roles.append("预算")
    return _ordered_review_roles(roles)


def _review_roles_from_text(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    direct = normalize_response_review_role(text)
    roles = [direct] if direct else []
    for role, keywords in ROLE_OWNER_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            roles.append(role)
    return _ordered_review_roles(roles)


def _ordered_review_roles(values: Any) -> list[str]:
    result: list[str] = []
    for role in values:
        text = normalize_response_review_role(str(role)) or str(role or "").strip()
        if text not in RESPONSE_REVIEW_ROLE_LABELS.values():
            continue
        if text not in result:
            result.append(text)
    order = {role: index for index, role in enumerate(RESPONSE_REVIEW_ROLE_LABELS.values())}
    return sorted(result, key=lambda role: order.get(role, 999))


def _enrich_response_item_task_metadata(items: list[TenderResponseItem]) -> int:
    updated_count = 0
    group_child_counts = _response_task_group_child_counts(items)
    for item in items:
        if _is_superseded_response_item(item):
            continue
        normalized = _normalized_value(item)
        primary_role = _compute_primary_review_role(item)
        supporting_roles = _ordered_review_roles(role for role in _response_item_review_roles(item) if role != primary_role)
        review_action = _review_action_for_response_item(item, primary_role)
        coverage_classification = _coverage_classification_for_response_item(item)
        granularity_level = normalized.get("granularity_level") or _granularity_level_for_response_item(item)
        quality_score = _quality_score_for_response_item(item, granularity_level)
        task_display = _task_display_metadata(item, primary_role, review_action, granularity_level, group_child_counts)
        done_checklist = _done_checklist_for_response_item(item, review_action, primary_role)
        priority = _review_priority_metadata(item, review_action, primary_role, str(task_display.get("task_display_type") or ""))
        next_normalized = dict(normalized)
        next_normalized.update(
            {
                "primary_review_role": primary_role,
                "supporting_roles": supporting_roles,
                "review_roles": _ordered_review_roles([primary_role] + supporting_roles),
                "review_action": review_action,
                "review_action_label": REVIEW_ACTION_LABELS.get(review_action, review_action),
                "done_criteria": _done_criteria_text(done_checklist, review_action),
                "done_checklist": done_checklist,
                "coverage_classification": coverage_classification,
                "granularity_level": granularity_level,
                "quality_score": quality_score,
                **task_display,
                **priority,
            }
        )
        if next_normalized != normalized:
            item.normalized_json = dumps_json(next_normalized)
            updated_count += 1
        if not item.reviewed_by and not item.reviewer_note and item.owner_role != primary_role:
            item.owner_role = primary_role
    return updated_count


def _response_task_group_child_counts(items: list[TenderResponseItem]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for item in items:
        if _is_superseded_response_item(item):
            continue
        normalized = _normalized_value(item)
        if _task_group_index(item, normalized) is None:
            continue
        primary_role = _compute_primary_review_role(item)
        review_action = _review_action_for_response_item(item, primary_role)
        group_key = _task_group_key(item, primary_role, review_action)
        if group_key:
            counts[group_key] += 1
    return counts


def _task_display_metadata(
    item: TenderResponseItem,
    primary_role: str,
    review_action: str,
    granularity_level: str,
    group_child_counts: Counter[str],
) -> dict[str, Any]:
    group_index = _task_group_index(item, _normalized_value(item))
    group_key = _task_group_key(item, primary_role, review_action)
    group_child_count = int(group_child_counts.get(group_key, 0)) if group_key else 0
    base_title = _task_group_base_title(item.response_title)

    if group_index is not None:
        display_type = "group_task"
    elif granularity_level == "risk_family":
        display_type = "summary_task"
    elif granularity_level == "theme_cluster":
        display_type = "theme_task"
    else:
        display_type = "single_task"

    return {
        "task_display_type": display_type,
        "task_display_label": TASK_DISPLAY_TYPE_LABELS.get(display_type, display_type),
        "task_group_key": group_key,
        "task_group_parent_title": base_title if group_index is not None else None,
        "task_group_index": group_index,
        "task_group_child_count": group_child_count if display_type == "summary_task" else 0,
        "has_group_children": display_type == "summary_task" and group_child_count > 0,
    }


def _task_group_key(item: TenderResponseItem | dict[str, Any], primary_role: str, review_action: str) -> str:
    category = str(_value(item, "response_category") or "")
    title = _task_group_base_title(str(_value(item, "response_title") or ""))
    if not title:
        return ""
    return "|".join([primary_role, review_action, category, _canonical_response_text(title)])


def _task_group_base_title(title: str | None) -> str:
    text = str(title or "").strip()
    return GROUP_TITLE_PATTERN.sub("", text).strip()


def _task_group_index(item: TenderResponseItem | dict[str, Any], normalized: dict[str, Any] | None = None) -> int | None:
    match = GROUP_TITLE_PATTERN.search(str(_value(item, "response_title") or ""))
    if match:
        raw = match.group(1) or match.group(2)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    normalized = normalized or _normalized_value(item)
    split_key = str(normalized.get("split_key") or "")
    match = re.search(r"_g(\d+)$", split_key)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _done_checklist_for_response_item(item: TenderResponseItem | dict[str, Any], review_action: str, primary_role: str) -> list[str]:
    text = _response_item_search_text_from_value(item)
    checklist: list[str]
    if any(token in text for token in ("漏项", "漏报", "漏填", "清单", "响应表", "偏离表")):
        checklist = [
            "核对清单、图纸、技术要求和响应表是否存在漏项或漏填。",
            "确认漏项责任是否转嫁给投标人，是否需要答疑、偏离或报价预留。",
            "形成可写入报价说明、响应表或法务意见的处理口径。",
        ]
    elif any(token in text for token in ("固定总价", "总价包干", "综合单价", "包干", "不调价", "不调整", "价格波动")):
        checklist = [
            "确认包干范围、暂定量、变更签证和调价边界。",
            "测算人工、材料、措施费或总价包干带来的费用预留。",
            "输出报价口径、合同偏离建议或无需偏离的确认说明。",
        ]
    elif any(token in text for token in ("人员", "项目经理", "到岗", "更换", "材料", "品牌", "样板")) and any(token in text for token in ("违约", "扣款", "处罚", "罚款")):
        checklist = [
            "核对人员到岗、更换限制、材料品牌、样板和进场验收触发条件。",
            "确认违约扣款上限、触发频次和是否需要成本预留。",
            "绑定人员/材料证明文件、报价说明或法务判断结论。",
        ]
    elif any(token in text for token in ("工期", "进度", "延期")) and any(token in text for token in ("违约", "扣款", "处罚", "罚款")):
        checklist = [
            "确认节点工期、总工期、延期责任和违约金计算口径。",
            "判断赶工、窝工、甲供/甲指配合延误是否需要报价预留或答疑。",
            "形成进度承诺、风险预留或合同偏离意见。",
        ]
    elif any(token in text for token in ("质量", "验收", "整改", "维修", "保修")) and any(token in text for token in ("违约", "扣款", "处罚", "责任")):
        checklist = [
            "确认质量验收、整改维修、保修责任和扣款触发条件。",
            "判断技术措施、检测复验和质保成本是否已覆盖。",
            "形成技术标响应、报价预留或法务责任边界意见。",
        ]
    elif any(token in text for token in ("解除", "停工", "退场", "终止", "重新发包")):
        checklist = [
            "确认解除、停工、退场和重新发包责任触发条件。",
            "评估停窝工、退场、二次进场和索赔受限带来的成本暴露。",
            "输出法务偏离、答疑问题或投标决策提示。",
        ]
    elif any(token in text for token in ("垫资", "预付款", "付款", "结算", "进度款", "回款")):
        checklist = [
            "确认预付款、进度款、结算付款和回款周期。",
            "测算垫资、现金流和资金成本影响。",
            "形成报价预留、商务澄清或合同风险意见。",
        ]
    elif review_action == "write_technical_document":
        checklist = [
            "确认该要求应写入技术标的章节和专项措施。",
            "补齐施工组织、质量、安全、进度或材料控制证明材料。",
            "确保技术响应与商务承诺、报价口径不冲突。",
        ]
    elif review_action == "prepare_qualification":
        checklist = [
            "核对资质、业绩、人员、证照和授权材料是否齐全。",
            "确认有效期、签章、格式和原件/复印件要求。",
            "绑定到投标文件清单并标记缺口。",
        ]
    elif review_action == "clarification_question":
        checklist = [
            "判断是否必须在答疑截止前提问。",
            "形成问题文本、涉及条款和希望甲方明确的口径。",
            "如不提问，记录不提问原因和内部处理方式。",
        ]
    else:
        checklist = [REVIEW_ACTION_DONE_CRITERIA.get(review_action, "已完成复核并形成可追溯说明。")]

    if primary_role == "法务" and not any("法务" in item for item in checklist):
        checklist.append("法务确认责任边界、偏离意见或可接受条件。")
    if primary_role == "预算" and not any("报价" in item or "费用" in item or "成本" in item for item in checklist):
        checklist.append("预算确认费用预留、报价口径和是否单列说明。")
    return checklist


def _done_criteria_text(checklist: list[str], review_action: str) -> str:
    if checklist:
        return "；".join(checklist[:3])
    return REVIEW_ACTION_DONE_CRITERIA.get(review_action, "已完成复核并形成可追溯说明。")


def _review_priority_metadata(
    item: TenderResponseItem | dict[str, Any],
    review_action: str,
    primary_role: str,
    task_display_type: str,
) -> dict[str, Any]:
    text = _response_item_search_text_from_value(item)
    coverage = _response_item_coverage(item)
    risk_count = len(_int_list(coverage.get("risk_ids")))
    requirement_count = len(_int_list(coverage.get("requirement_ids")))
    risk_level = str(_value(item, "risk_level") or "").lower()
    blocking_terms = ("废标", "否决", "重大偏差", "不响应", "投标截止", "保证金", "投标有效期")
    high_exposure_terms = ("漏项", "违约", "扣款", "固定总价", "总价包干", "不调价", "不调整", "垫资", "付款", "解除", "停工")

    if risk_level in {"blocking", "critical"} or any(token in text for token in blocking_terms):
        priority = "P0"
        reason = "涉及废标、截止、保证金、重大偏差或投标决策，需第一波处理。"
    elif task_display_type == "group_task":
        priority = "P2"
        reason = "属于汇总任务下的分组复核项，先由负责人看汇总，再在第二波展开处理。"
    elif risk_level == "high" or any(token in text for token in high_exposure_terms) or risk_count >= 8 or requirement_count >= 30:
        priority = "P1"
        reason = "涉及高责任、高金额、价格边界或大量证据，需第一波完成预算/法务判断。"
    elif primary_role in {"预算", "法务", "技术"} or review_action in {"prepare_qualification", "write_business_document", "write_technical_document"}:
        priority = "P2"
        reason = "属于必须响应或专业复核事项，安排第二波补齐。"
    else:
        priority = "P3"
        reason = "常规响应确认，可在第三波补齐和关闭。"

    wave = "wave_1" if priority in {"P0", "P1"} else "wave_2" if priority == "P2" else "wave_3"
    return {
        "review_priority": priority,
        "review_priority_label": REVIEW_PRIORITY_LABELS[priority],
        "review_wave": wave,
        "review_wave_label": REVIEW_WAVE_LABELS[wave],
        "priority_reason": reason,
    }


def _response_item_primary_review_role(item: TenderResponseItem | dict[str, Any]) -> str | None:
    normalized = _normalized_value(item)
    stored = normalize_response_review_role(str(normalized.get("primary_review_role") or ""))
    if stored:
        return stored
    return _compute_primary_review_role(item)


def _response_item_supporting_roles(item: TenderResponseItem | dict[str, Any]) -> list[str]:
    normalized = _normalized_value(item)
    if isinstance(normalized.get("supporting_roles"), list):
        roles = _ordered_review_roles(normalized["supporting_roles"])
        if roles:
            return [role for role in roles if role != _response_item_primary_review_role(item)]
    primary = _response_item_primary_review_role(item)
    return _ordered_review_roles(role for role in _response_item_review_roles(item) if role != primary)


def _compute_primary_review_role(item: TenderResponseItem | dict[str, Any]) -> str:
    category = str(_value(item, "response_category") or "")
    action = str(_value(item, "response_action") or "")
    status_value = str(_value(item, "status") or "")
    text = _response_item_search_text_from_value(item)
    if category in TECHNICAL_RESPONSE_CATEGORIES:
        return "技术"
    if action == "legal_review" or status_value == "legal_review":
        return "法务"
    if action == "quote_allowance" or status_value == "to_quote_allowance":
        return "预算"
    if action == "clarification":
        return "经营"
    if category in {"qualification", "document_checklist", "bid_rule"}:
        return "经营"
    if action == "document_preparation" and any(keyword in text for keyword in TECHNICAL_RESPONSE_KEYWORDS):
        return "技术"
    if category == "contract_clause" and any(keyword in text for keyword in LEGAL_RESPONSE_KEYWORDS):
        return "法务"
    if category == "pricing_constraint" or any(keyword in text for keyword in BUDGET_RESPONSE_KEYWORDS):
        return "预算"
    return PRIMARY_ROLE_BY_ACTION.get(action) or normalize_response_review_role(str(_value(item, "owner_role") or "")) or "经营"


def _review_action_for_response_item(item: TenderResponseItem | dict[str, Any], primary_role: str) -> str:
    action = str(_value(item, "response_action") or "")
    status_value = str(_value(item, "status") or "")
    if action == "clarification" or status_value == "to_clarify":
        return "clarification_question"
    if primary_role == "预算":
        return "budget_assessment"
    if primary_role == "法务":
        return "legal_assessment"
    if primary_role == "技术":
        return "write_technical_document"
    if action == "qualification_material":
        return "prepare_qualification"
    if action == "document_preparation":
        return "write_business_document"
    return "confirm_response"


def _coverage_classification_for_response_item(item: TenderResponseItem | dict[str, Any]) -> str:
    action = str(_value(item, "response_action") or "")
    category = str(_value(item, "response_category") or "")
    risk_level = str(_value(item, "risk_level") or "")
    if action == "reference":
        return "evidence_reference"
    if risk_level == "low" and category not in {"bid_rule", "qualification", "document_checklist", "technical_requirement"}:
        return "evidence_reference"
    return "must_respond"


def _granularity_level_for_response_item(item: TenderResponseItem | dict[str, Any]) -> str:
    normalized = _normalized_value(item)
    if normalized.get("source") == "requirement_cluster" or _value(item, "created_from") == "requirement_cluster":
        return "theme_cluster"
    if normalized.get("source") == "quality_split" or _value(item, "created_from") == "quality_split":
        return "role_action"
    coverage = _response_item_coverage(item)
    risk_count = len(_int_list(coverage.get("risk_ids")))
    requirement_count = len(_int_list(coverage.get("requirement_ids")))
    if risk_count >= 8 or requirement_count >= 15:
        return "bundle"
    return "atomic"


def _quality_score_for_response_item(item: TenderResponseItem | dict[str, Any], granularity_level: str) -> float:
    coverage = _response_item_coverage(item)
    risk_count = len(_int_list(coverage.get("risk_ids")))
    requirement_count = len(_int_list(coverage.get("requirement_ids")))
    score = 0.9
    if granularity_level == "bundle":
        score -= 0.2
    if risk_count >= 8:
        score -= 0.1
    if requirement_count >= 15:
        score -= 0.1
    if _normalized_value(item).get("quality_flags"):
        score += 0.03
    return round(max(0.45, min(0.98, score)), 2)


def _response_item_search_text_from_value(item: TenderResponseItem | dict[str, Any]) -> str:
    if isinstance(item, TenderResponseItem):
        return _response_item_search_text(item)
    normalized = _normalized_value(item)
    values = [
        _value(item, "response_title"),
        _value(item, "response_note"),
        _value(item, "source_text"),
        _value(item, "response_category"),
        _value(item, "response_action"),
        normalized.get("object_type"),
        normalized.get("object_subtype"),
        normalized.get("risk_type"),
    ]
    return " ".join(str(value or "") for value in values).lower()


def _covered_source_ids(business_objects: list[TenderBusinessObject]) -> tuple[set[int], set[int]]:
    requirement_ids: set[int] = set()
    risk_ids: set[int] = set()
    for item in business_objects:
        if item.review_status == "ignored" or not item.response_required:
            continue
        if item.requirement_id:
            requirement_ids.add(item.requirement_id)
        if item.risk_id:
            risk_ids.add(item.risk_id)
        requirement_ids.update(_int_list(loads_json(item.related_requirement_ids_json, []) if item.related_requirement_ids_json else []))
        risk_ids.update(_int_list(loads_json(item.related_risk_ids_json, []) if item.related_risk_ids_json else []))
    return requirement_ids, risk_ids


def _technical_requirement_cluster_key(requirement: TenderRequirement) -> str:
    text = f"{requirement.parsed_requirement or ''} {requirement.original_text or ''}".lower()
    best_key = "other_technical"
    best_score = 0
    for key, config in TECHNICAL_REQUIREMENT_CLUSTERS.items():
        score = 0
        for keyword in config.get("keywords", ()):
            if str(keyword).lower() in text:
                score += max(1, len(str(keyword)) // 2)
        if score > best_score:
            best_key = key
            best_score = score
    return best_key


def _cluster_sort_key(cluster_key: str, items: list[TenderRequirement]) -> tuple[int, str]:
    order = list(TECHNICAL_REQUIREMENT_CLUSTERS).index(cluster_key) if cluster_key in TECHNICAL_REQUIREMENT_CLUSTERS else 999
    return order, items[0].requirement_uuid if items else cluster_key


def _response_title_from_requirement(requirement: TenderRequirement) -> str:
    if requirement.requirement_type == "technical":
        cluster_key = _technical_requirement_cluster_key(requirement)
        config = TECHNICAL_REQUIREMENT_CLUSTERS.get(cluster_key)
        if config:
            return str(config["title"])
    text = _short_text(requirement.parsed_requirement or requirement.original_text or requirement.requirement_type, 80)
    return text or requirement.requirement_type or "招标要求响应项"


def _secondary_business_actions(normalized: dict[str, Any], effective: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("secondary_business_actions", "risk_secondary_actions"):
        value = effective.get(key)
        if not isinstance(value, list):
            value = normalized.get(key)
        if isinstance(value, list):
            result.extend(str(item) for item in value if item)
    return _dedupe_strings(result)


def _risk_types_from_business_object(item: TenderBusinessObject, normalized: dict[str, Any], evidence: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    if item.risk and item.risk.risk_type:
        result.append(item.risk.risk_type)
    for entry in evidence:
        if isinstance(entry, dict) and entry.get("risk_type"):
            result.append(str(entry["risk_type"]))
    risk_cards = normalized.get("risk_cards") if isinstance(normalized.get("risk_cards"), list) else []
    for card in risk_cards:
        if isinstance(card, dict) and card.get("risk_type"):
            result.append(str(card["risk_type"]))
    return _dedupe_strings(result)


def _workflow_actions(
    primary_action: str,
    *,
    category: str,
    risk_types: list[str],
    object_subtype: str | None,
    secondary_business_actions: list[str],
) -> list[dict[str, str]]:
    actions: list[str] = [primary_action]
    for action in secondary_business_actions:
        actions.append(_response_action_from_business_action(action))
    normalized_subtype = str(object_subtype or "")
    for risk_type in risk_types:
        risk_text = str(risk_type or "")
        if risk_text in QUOTE_RISK_TYPES:
            actions.append("quote_allowance")
        if risk_text in LEGAL_RISK_TYPES:
            actions.append("legal_review")
        if risk_text in {"claim_time_limit", "site_condition", "design_or_drawing_unclear"}:
            actions.append("clarification")
    if category in {"contract_clause", "pricing_constraint", "risk"}:
        if any(token in normalized_subtype for token in ("payment", "付款", "settlement", "结算", "advance", "垫资")):
            actions.extend(["quote_allowance", "legal_review"])
        if any(token in normalized_subtype for token in ("penalty", "违约", "liquidated", "扣款")):
            actions.extend(["quote_allowance", "legal_review"])
        if any(token in normalized_subtype for token in ("claim", "签证", "索赔", "time_limit")):
            actions.extend(["clarification", "legal_review"])
        if any(token in normalized_subtype for token in ("price", "价格", "adjust", "调价", "omission", "漏项")):
            actions.extend(["quote_allowance", "legal_review"])
    actions = [action for action in _dedupe_strings(actions) if action in RESPONSE_ACTIONS and action != "reference"]
    return [
        {
            "action": action,
            "label": RESPONSE_ACTION_LABELS.get(action, action),
            "owner_role": _owner_from_response_action(action),
            "reason": _workflow_action_reason(action, primary_action),
        }
        for action in actions
    ]


def _workflow_action_reason(action: str, primary_action: str) -> str:
    if action == primary_action:
        return "主响应动作"
    if action == "quote_allowance":
        return "需预算测算风险预留或报价口径"
    if action == "legal_review":
        return "需复核合同责任、付款或违约条款"
    if action == "clarification":
        return "需判断是否形成标前答疑问题"
    if action == "document_preparation":
        return "需写入技术/商务响应文件"
    if action == "qualification_material":
        return "需准备资质、业绩或人员证明材料"
    return "协同处理动作"


def _coverage_payload(
    *,
    source_label: str,
    requirement_ids: list[int],
    risk_ids: list[int],
    evidence: list[dict[str, Any]],
    source_count: int,
    explanation_prefix: str,
) -> dict[str, Any]:
    requirement_ids = _int_list(requirement_ids)
    risk_ids = _int_list(risk_ids)
    evidence_count = len(evidence)
    parts = [explanation_prefix]
    if requirement_ids:
        parts.append(f"覆盖 {len(requirement_ids)} 条要求")
    if risk_ids:
        parts.append(f"覆盖 {len(risk_ids)} 条风险")
    parts.append(f"保留 {evidence_count} 条代表证据")
    if source_count > evidence_count:
        parts.append(f"来源样本 {source_count} 条")
    return {
        "source_label": source_label,
        "requirement_ids": requirement_ids,
        "risk_ids": risk_ids,
        "evidence_count": evidence_count,
        "source_count": source_count,
        "explanation": "，".join(parts) + "。",
    }


def _response_action_from_business_action(action: str) -> str:
    return BUSINESS_ACTION_TO_RESPONSE_ACTION.get(action, "direct_response")


def _response_action_from_risk(risk: TenderRisk) -> str:
    if risk.review_status == "to_clarify":
        return "clarification"
    if risk.review_status == "to_quote_allowance":
        return "quote_allowance"
    if risk.risk_type in QUOTE_RISK_TYPES:
        return "quote_allowance"
    if risk.risk_type in LEGAL_RISK_TYPES or risk.is_blocking:
        return "legal_review"
    return "direct_response"


def _response_action_from_requirement(requirement: TenderRequirement) -> str:
    if requirement.requirement_type == "qualification":
        return "qualification_material"
    if requirement.requirement_type in {"submission", "commercial", "technical", "brand"}:
        return "document_preparation"
    if requirement.requirement_type in {"bill", "schedule"}:
        return "direct_response"
    return "reference"


def _initial_status(review_status: str | None, response_action: str) -> str:
    if review_status in {"confirmed", "ignored", "to_clarify", "to_quote_allowance"}:
        return str(review_status)
    if response_action == "clarification":
        return "to_clarify"
    if response_action == "quote_allowance":
        return "to_quote_allowance"
    if response_action == "legal_review":
        return "legal_review"
    return "pending"


def _risk_level_from_business_object(item: TenderBusinessObject, normalized: dict[str, Any]) -> str:
    risk_grades = normalized.get("risk_grades") if isinstance(normalized.get("risk_grades"), list) else []
    if any(str(grade) in {"blocking", "critical", "high"} for grade in risk_grades):
        return "high"
    if any(str(grade) == "medium" for grade in risk_grades):
        return "medium"
    evidence = loads_json(item.evidence_json, []) if item.evidence_json else []
    levels = [
        str(entry.get("risk_level"))
        for entry in evidence
        if isinstance(entry, dict) and entry.get("risk_level")
    ]
    if "high" in levels:
        return "high"
    if "medium" in levels:
        return "medium"
    return "low"


def _owner_from_response_action(action: str) -> str:
    if action == "quote_allowance":
        return "预算"
    if action == "legal_review":
        return "法务"
    if action == "qualification_material":
        return "经营"
    if action == "document_preparation":
        return "经营"
    if action == "clarification":
        return "经营"
    return "经营"


def _compact_evidence(value: Any, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "source_kind": item.get("source_kind"),
                "source_file": item.get("source_file"),
                "source_location": item.get("source_location"),
                "document_section": item.get("document_section"),
                "risk_type": item.get("risk_type"),
                "risk_level": item.get("risk_level"),
                "risk_card_title": item.get("risk_card_title"),
                "original_text": item.get("original_text") or item.get("text"),
            }
        )
        if len(result) >= limit:
            break
    return result


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: set[int] = set()
    for item in value:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(result)


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _max_risk_level(values: list[str | None]) -> str:
    levels = {str(value or "low") for value in values}
    if "high" in levels or "blocking" in levels or "critical" in levels:
        return "high"
    if "medium" in levels:
        return "medium"
    return "low"


def _short_text(value: str | None, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _normalized_value(item: TenderResponseItem | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict):
        normalized = item.get("normalized") or item.get("normalized_json") or {}
        if isinstance(normalized, str):
            normalized = loads_json(normalized, {})
        return normalized if isinstance(normalized, dict) else {}
    return loads_json(item.normalized_json, {}) if item.normalized_json else {}


def _value(item: TenderResponseItem | dict[str, Any], field: str) -> Any:
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)
