"""Automatic quote-preview pricing: account quota -> enterprise quota -> AI."""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account_quota import ACCOUNT_QUOTA_STATUS_ACTIVE, AccountQuotaItem
from app.models.user import User
from app.services.account_tenancy import AccountTenancyError, resolve_current_account
from app.services.budget_pricing import (
    BudgetPricingError,
    _NUMERIC_20_6_MAX,
    _QuotaEntry,
    _build_catalog_index,
    _decimal,
    _decimal_text,
    _fits_numeric,
    _load_quota_catalog,
    _match_source,
    _normalize_text,
    _q6,
    normalize_pricing_unit,
    strict_active_quota_version,
)
from app.services.budget_pricing_ai_estimates import (
    AI_ESTIMATE_ENGINE_VERSION,
    _rule_estimate,
    generate_budget_pricing_ai_estimate_batch,
)
from app.services.budget_pricing_drafts import _match_active_account_quota_source
from app.services.budget_pricing_match_v2_shadow import (
    SHADOW_DECISION_AUTO,
    SHADOW_DECISION_NONE,
    SHADOW_DECISION_REVIEW,
    SHADOW_MATCHING_ENGINE_VERSION,
    shadow_match_source,
)
from app.services.construction_notes import construction_note_only


logger = logging.getLogger(__name__)

CASCADE_ENGINE_VERSION = "quote-pricing-cascade-v1"
CASCADE_PRIORITY = ("account_quota", "enterprise_quota", "ai_estimate")
AI_BATCH_SIZE = 6
AI_CONCURRENCY = 3


def _account_snapshot(item: AccountQuotaItem) -> dict[str, Any]:
    return {
        "id": int(item.id),
        "item_uuid": item.item_uuid,
        "account_id": int(item.account_id),
        "quota_code": item.quota_code,
        "item_name": item.item_name,
        "item_features": item.item_features,
        "spec": item.spec,
        "unit": item.unit,
        "unit_price": _decimal_text(item.unit_price),
        "source": item.source,
        "status": item.status,
        "revision": int(item.revision),
        "notes": item.notes,
    }


def _load_account_catalog(
    db: Session,
    *,
    current_user: User,
) -> tuple[list[_QuotaEntry], dict[str, Any]]:
    try:
        account = resolve_current_account(db, current_user)
    except AccountTenancyError as exc:
        return [], {
            "status": "skipped",
            "reason": exc.code,
            "account_id": None,
            "active_item_count": 0,
            "eligible_item_count": 0,
        }

    rows = (
        db.query(AccountQuotaItem)
        .filter(
            AccountQuotaItem.account_id == account.id,
            AccountQuotaItem.status == ACCOUNT_QUOTA_STATUS_ACTIVE,
        )
        .order_by(AccountQuotaItem.id.asc())
        .all()
    )
    entries: list[_QuotaEntry] = []
    for item in rows:
        item_name = str(item.item_name or "").strip()
        unit = str(item.unit or "").strip()
        normalized_unit = normalize_pricing_unit(unit)
        price = _q6(_decimal(item.unit_price))
        if (
            not item_name
            or not unit
            or not normalized_unit
            or price is None
            or price <= 0
            or not _fits_numeric(price, _NUMERIC_20_6_MAX)
        ):
            continue
        feature_and_spec = " ".join(
            str(value).strip()
            for value in (item.item_features, item.spec)
            if str(value or "").strip()
        )
        snapshot = _account_snapshot(item)
        entries.append(
            _QuotaEntry(
                item_id=int(item.id),
                version_id=0,
                quota_code=str(item.quota_code or "").strip() or None,
                item_name=item_name,
                work_content=feature_and_spec or None,
                worker_or_subtype=None,
                unit=unit,
                normalized_unit=normalized_unit,
                unit_price=price,
                labor_fee=None,
                main_material_fee=None,
                auxiliary_material_fee=None,
                machinery_fee=None,
                name_norm=_normalize_text(item_name),
                spec_norm=_normalize_text(feature_and_spec),
                code_norm=_normalize_text(item.quota_code),
                snapshot=snapshot,
                full_snapshot=snapshot,
            )
        )
    return entries, {
        "status": "available",
        "reason": None,
        "account_id": int(account.id),
        "account_name": account.account_name,
        "active_item_count": len(rows),
        "eligible_item_count": len(entries),
    }


def _load_enterprise_catalog(db: Session) -> tuple[list[_QuotaEntry], dict[str, Any]]:
    try:
        version = strict_active_quota_version(db)
        catalog, stats = _load_quota_catalog(db, version)
    except BudgetPricingError as exc:
        return [], {
            "status": "skipped",
            "reason": exc.code,
            "version_id": None,
            "eligible_item_count": 0,
        }
    return catalog, {
        "status": "available",
        "reason": None,
        "version_id": int(version.id),
        "version_code": version.version_code,
        "version_name": version.version_name,
        **stats,
    }


def _quota_code(row: dict[str, Any]) -> str | None:
    for key in ("quota_code", "enterprise_quota_code", "item_code"):
        value = str(row.get(key) or "").strip()
        if value:
            return value[:64]
    raw_fields = row.get("raw_fields")
    if isinstance(raw_fields, dict):
        for key, value in raw_fields.items():
            if "定额编码" in str(key) or "项目编码" in str(key):
                cleaned = str(value or "").strip()
                if cleaned:
                    return cleaned[:64]
    return None


def _source_values(row: dict[str, Any], index: int) -> dict[str, Any]:
    quantity = _q6(_decimal(row.get("quantity")))
    quantity_resolved = bool(quantity is not None and quantity > 0)
    return {
        "row_key": str(row.get("requirement_row_key") or f"quote:{index + 1:04d}")[:128],
        "source_sheet": str(row.get("source_sheet") or "需求清单")[:255],
        "raw_row_index": int(row.get("raw_row_index") or index + 1),
        "item_name": str(row.get("item_name") or row.get("project_name") or "").strip()[:255],
        "spec": str(row.get("spec") or "").strip(),
        "unit": str(row.get("unit") or "").strip()[:64],
        "normalized_unit": normalize_pricing_unit(row.get("unit")),
        "quota_code": _quota_code(row),
        "quantity": quantity,
        "quantity_resolved": quantity_resolved,
        "quantity_status": "valid" if quantity_resolved else "missing",
        "remark": str(row.get("remark") or row.get("notes") or "").strip(),
        "raw_text": str(row.get("raw_text") or "").strip(),
        "standardization": {
            "confidence": row.get("confidence"),
            "warnings": list(row.get("warnings") or []),
            "requires_confirmation": bool(row.get("requires_confirmation")),
            "auto_mapped": bool(row.get("auto_mapped", True)),
        },
        "snapshot": row,
    }


def _match_attempt(match: dict[str, Any], *, tier: str) -> dict[str, Any]:
    candidates = match.get("candidates") or []
    return {
        "tier": tier,
        "status": match.get("match_status"),
        "rule": (match.get("reason") or {}).get("rule"),
        "candidate_ids": [
            int(record["entry"].item_id)
            for record in candidates
            if isinstance(record, dict) and record.get("entry") is not None
        ],
    }


def _price_breakdown(entry: _QuotaEntry) -> dict[str, Any]:
    return {
        "labor_unit_cost": _decimal_text(entry.labor_fee),
        "main_material_unit_cost": _decimal_text(entry.main_material_fee),
        "auxiliary_material_unit_cost": _decimal_text(entry.auxiliary_material_fee),
        "machinery_unit_cost": _decimal_text(entry.machinery_fee),
    }


def _enterprise_v2_candidate(
    record: dict[str, Any],
    *,
    enterprise_meta: dict[str, Any],
) -> dict[str, Any]:
    entry: _QuotaEntry = record["entry"]
    snapshot = dict(entry.snapshot)
    section = snapshot.get("section") if isinstance(snapshot.get("section"), dict) else {}
    score = _q6(_decimal(record.get("score"))) or Decimal("0")
    detail_url = f"/admin/cost-db?enterprise_quota_item_id={int(entry.item_id)}"
    return {
        "id": int(entry.item_id),
        "quota_item_id": int(entry.item_id),
        "enterprise_quota_item_id": int(entry.item_id),
        "enterprise_quota_version_id": int(entry.version_id),
        "enterprise_quota_version_code": enterprise_meta.get("version_code"),
        "enterprise_quota_version_name": enterprise_meta.get("version_name"),
        "quota_code": entry.quota_code,
        "item_name": entry.item_name,
        "spec": entry.work_content,
        "work_content": entry.work_content,
        "unit": entry.unit,
        "price": float(entry.unit_price or 0),
        "unit_price": float(entry.unit_price or 0),
        "status": "active",
        "reference_source": "enterprise_quota.active",
        "reference_price_source": "enterprise_quota_unit_price",
        "source_type": "enterprise_quota_item",
        "price_type": "enterprise_quota_unit_price",
        "category": section.get("section_name") or "企业定额",
        "subcategory": entry.worker_or_subtype,
        "section_code": section.get("section_code"),
        "section_name": section.get("section_name"),
        "cost_item_url": detail_url,
        "evidence_url": detail_url,
        "enterprise_quota_v2_candidate": True,
        "v2_score": float(score),
        "v2_name_score": float(record.get("name_score") or 0),
        "v2_concept_score": float(record.get("concept_score") or 0),
        "v2_structured_score": float(record.get("structured_score") or 0),
        "v2_unit_score": float(record.get("unit_score") or 0),
        "v2_unit_rule": record.get("unit_rule"),
        "v2_risk_flags": list(record.get("risk_flags") or []),
        "v2_match_reason": record.get("reason"),
        "notes": f"企业定额匹配分数 {score}；{record.get('reason') or ''}".strip("；"),
    }


def _enterprise_v2_review(
    source: dict[str, Any],
    catalog: list[_QuotaEntry],
    *,
    enterprise_meta: dict[str, Any],
) -> dict[str, Any] | None:
    if not settings.feature_enterprise_quota_v2_review or not catalog:
        return None
    match = shadow_match_source(source, catalog)
    decision = str(match.get("decision") or SHADOW_DECISION_NONE)
    review_required = decision in {SHADOW_DECISION_AUTO, SHADOW_DECISION_REVIEW}
    records = list(match.get("candidates") or []) if review_required else []
    candidates = [
        _enterprise_v2_candidate(record, enterprise_meta=enterprise_meta)
        for record in records
    ]
    recommended = candidates[0] if candidates else None
    decision_label = {
        SHADOW_DECISION_AUTO: "企业定额高置信命中",
        SHADOW_DECISION_REVIEW: "企业定额命中待复核",
        SHADOW_DECISION_NONE: "企业定额未命中",
    }.get(decision, "企业定额未命中")
    return {
        "schema": "enterprise-quota-v2-review/v1",
        "engine_version": SHADOW_MATCHING_ENGINE_VERSION,
        "decision": decision,
        "decision_label": decision_label,
        "rule": match.get("rule"),
        "top_score": float(match.get("top_score") or 0),
        "runner_up_score": float(match.get("runner_up_score") or 0),
        "margin": float(match.get("margin") or 0),
        "thresholds": dict(match.get("thresholds") or {}),
        "candidate_count": len(candidates),
        "recommended_candidate": recommended,
        "recommended_candidate_id": recommended.get("id") if recommended else None,
        "candidates": candidates,
        "requires_manual_confirmation": review_required,
        "manual_confirmation_status": "pending" if review_required else "not_required",
        "manual_action": None,
        "selected_candidate_id": None,
        "message": (
            "企业定额已命中推荐结果，但不会自动覆盖当前AI估价；请人工采用推荐、改选或确认继续使用AI估价。"
            if review_required
            else "企业定额未形成达到人工复核阈值的命中结果。"
        ),
    }


def _matched_reference(
    source: dict[str, Any],
    entry: _QuotaEntry,
    *,
    tier: str,
    match: dict[str, Any],
    enterprise_meta: dict[str, Any],
) -> dict[str, Any]:
    is_account = tier == "account_quota"
    source_label = "账户定额" if is_account else "企业定额"
    reference_source = "account_quota.active" if is_account else "enterprise_quota.active"
    price_source = "account_quota_unit_price" if is_account else "enterprise_quota_unit_price"
    match_label = f"{source_label}匹配"
    snapshot = dict(entry.snapshot)
    source_item = {
        "id": int(entry.item_id),
        "item_name": entry.item_name,
        "spec": entry.work_content,
        "unit": entry.unit,
        "price": _decimal_text(entry.unit_price),
        "status": "active",
        "reference_source": reference_source,
        "source_type": "account_quota_item" if is_account else "enterprise_quota_item",
        "quota_code": entry.quota_code,
        **snapshot,
    }
    reference: dict[str, Any] = {
        "matched": True,
        "match_type": f"{tier}_matched",
        "match_type_label": match_label,
        "match_reason": (match.get("reason") or {}).get("rule"),
        "cost_item_id": int(entry.item_id),
        "reference_source": reference_source,
        "source_type": "account_quota_item" if is_account else "enterprise_quota_item",
        "quota_code": entry.quota_code,
        "item_name": entry.item_name,
        "spec": entry.work_content,
        "unit": entry.unit,
        "reference_price": float(entry.unit_price or 0),
        "reference_price_source": price_source,
        "reference_price_source_label": f"{source_label}单价",
        "ai_unit_price": float(entry.unit_price or 0),
        "price_delta": 0.0,
        "price_delta_rate": 0.0,
        "fallback_applied": False,
        "ai_price_source": f"{tier}_adopted",
        "ai_price_source_label": source_label,
        "ai_price_source_reason": f"本行按报价优先级命中{source_label} active 条目，未调用后续价格层。",
        "source_cost_item": source_item,
        "price_breakdown": _price_breakdown(entry),
        "source_requirement_row_key": source["row_key"],
        "source_requirement_project_name": source["item_name"],
        "source_requirement_spec": source["spec"],
        "source_requirement_quantity": _decimal_text(source["quantity"]),
        "source_requirement_unit": source["unit"],
        "source_requirement_remark": source["remark"],
        "source_requirement_raw_text": source["raw_text"],
    }
    if is_account:
        reference.update(
            {
                "account_id": snapshot.get("account_id"),
                "account_quota_item_id": int(entry.item_id),
                "account_quota_item_uuid": snapshot.get("item_uuid"),
                "account_quota_item_revision": snapshot.get("revision"),
            }
        )
    else:
        section = snapshot.get("section") if isinstance(snapshot.get("section"), dict) else {}
        reference.update(
            {
                "enterprise_quota_version_id": int(entry.version_id),
                "enterprise_quota_version_code": enterprise_meta.get("version_code"),
                "enterprise_quota_version_name": enterprise_meta.get("version_name"),
                "enterprise_quota_item_id": int(entry.item_id),
                "section_code": section.get("section_code"),
                "section_name": section.get("section_name"),
                "work_content": entry.work_content,
            }
        )
    return reference


def _no_match_reference(
    source: dict[str, Any],
    *,
    unit_price: Decimal,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "matched": False,
        "match_type": None,
        "match_type_label": "AI估价",
        "reference_price": None,
        "ai_unit_price": float(unit_price),
        "price_delta": None,
        "price_delta_rate": None,
        "message": "账户定额和企业定额均未命中，已进入 AI 估价。",
        "pricing_match_attempts": attempts,
        "source_requirement_row_key": source["row_key"],
        "source_requirement_project_name": source["item_name"],
        "source_requirement_spec": source["spec"],
        "source_requirement_quantity": _decimal_text(source["quantity"]),
        "source_requirement_unit": source["unit"],
        "source_requirement_remark": source["remark"],
        "source_requirement_raw_text": source["raw_text"],
    }


def _total(quantity: Decimal | None, unit_price: Decimal | None) -> Decimal:
    if quantity is None or quantity <= 0 or unit_price is None or unit_price <= 0:
        return Decimal("0.00")
    return (quantity * unit_price).quantize(Decimal("0.01"))


async def _ai_estimates(
    snapshots: list[dict[str, Any]],
    *,
    current_user: User,
) -> dict[str, dict[str, Any]]:
    if not snapshots:
        return {}
    chunks = [
        snapshots[index : index + AI_BATCH_SIZE]
        for index in range(0, len(snapshots), AI_BATCH_SIZE)
    ]
    semaphore = asyncio.Semaphore(AI_CONCURRENCY)

    async def run_chunk(chunk: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        async with semaphore:
            try:
                return await generate_budget_pricing_ai_estimate_batch(
                    chunk,
                    current_user=current_user,
                )
            except Exception as exc:
                logger.exception(
                    "quote_pricing_cascade_ai_batch_failed",
                    extra={"event": "quote_pricing_cascade_ai_batch_failed", "row_count": len(chunk)},
                )
                fallback: dict[str, dict[str, Any]] = {}
                for snapshot in chunk:
                    row_id = str(snapshot.get("source_row_key") or "")
                    estimate = _rule_estimate(snapshot, fallback_reason=type(exc).__name__)
                    estimate.update(
                        {
                            "provider": "rule",
                            "model": None,
                            "prompt_version": "quote-pricing-cascade-fallback",
                            "engine_version": AI_ESTIMATE_ENGINE_VERSION,
                            "batch_mode": True,
                        }
                    )
                    fallback[row_id] = estimate
                return fallback

    results: dict[str, dict[str, Any]] = {}
    for chunk_result in await asyncio.gather(*(run_chunk(chunk) for chunk in chunks)):
        results.update(chunk_result)
    return results


async def build_quote_pricing_cascade_preview(
    db: Session,
    *,
    standard_rows: list[dict[str, Any]],
    current_user: User,
    standardization_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a complete preview while invoking AI only for unmatched rows."""

    sources = [_source_values(row, index) for index, row in enumerate(standard_rows)]
    account_catalog, account_meta = _load_account_catalog(db, current_user=current_user)
    enterprise_catalog, enterprise_meta = _load_enterprise_catalog(db)
    enterprise_index = _build_catalog_index(enterprise_catalog)

    planned: list[dict[str, Any]] = []
    ai_snapshots: list[dict[str, Any]] = []
    for source in sources:
        attempts: list[dict[str, Any]] = []
        selected_entry: _QuotaEntry | None = None
        selected_tier: str | None = None
        selected_match: dict[str, Any] | None = None
        enterprise_v2_review: dict[str, Any] | None = None

        account_match = _match_active_account_quota_source(source, account_catalog)
        attempts.append(_match_attempt(account_match, tier="account_quota"))
        account_selected = account_match.get("selected")
        if account_selected is not None and account_selected["entry"].unit_price:
            selected_entry = account_selected["entry"]
            selected_tier = "account_quota"
            selected_match = account_match
        else:
            enterprise_match = _match_source(source, enterprise_catalog, enterprise_index)
            attempts.append(_match_attempt(enterprise_match, tier="enterprise_quota"))
            enterprise_selected = enterprise_match.get("selected")
            if enterprise_selected is not None and enterprise_selected["entry"].unit_price:
                selected_entry = enterprise_selected["entry"]
                selected_tier = "enterprise_quota"
                selected_match = enterprise_match
            else:
                enterprise_v2_review = _enterprise_v2_review(
                    source,
                    enterprise_catalog,
                    enterprise_meta=enterprise_meta,
                )
                recommended_candidate = (
                    enterprise_v2_review.get("recommended_candidate")
                    if isinstance(enterprise_v2_review, dict)
                    else None
                )
                recommended_id = (
                    int(recommended_candidate.get("id"))
                    if isinstance(recommended_candidate, dict) and recommended_candidate.get("id")
                    else None
                )
                recommended_entry = next(
                    (
                        entry
                        for entry in enterprise_catalog
                        if recommended_id is not None and int(entry.item_id) == recommended_id
                    ),
                    None,
                )
                if recommended_entry is not None and recommended_entry.unit_price:
                    selected_entry = recommended_entry
                    selected_tier = "enterprise_quota"
                    selected_match = {
                        "reason": {"rule": "enterprise_semantic_match_auto_adopt"},
                        "selected": {"entry": recommended_entry},
                    }
                    enterprise_v2_review.update(
                        {
                            "requires_manual_confirmation": False,
                            "manual_confirmation_status": "auto_adopted",
                            "manual_action": "auto_adopt_recommended",
                            "selected_candidate_id": recommended_id,
                            "message": "企业定额已命中并自动采用首选定额价格。",
                        }
                    )

        plan = {
            "source": source,
            "entry": selected_entry,
            "tier": selected_tier,
            "match": selected_match,
            "attempts": attempts,
            "enterprise_v2_review": enterprise_v2_review,
        }
        planned.append(plan)
        if selected_entry is None:
            ai_snapshots.append(
                {
                    "line_uuid": source["row_key"],
                    "source_row_key": source["row_key"],
                    "source_sheet": source["source_sheet"],
                    "source_raw_row_index": source["raw_row_index"],
                    "item_name": source["item_name"],
                    "spec": source["spec"],
                    "unit": source["unit"],
                    "normalized_unit": source["normalized_unit"],
                    "calculation_quantity": _decimal_text(source["quantity"]),
                    "quantity_status": source["quantity_status"],
                    "pricing_mode": "unified_quote_preview",
                    "summary_context": {},
                    "reference_context": {
                        "priority": list(CASCADE_PRIORITY),
                        "account_attempt": attempts[0],
                        "enterprise_attempt": attempts[1] if len(attempts) > 1 else None,
                    },
                }
            )

    ai_results = await _ai_estimates(ai_snapshots, current_user=current_user)
    preview_rows: list[dict[str, Any]] = []
    counts = {"account_quota": 0, "enterprise_quota": 0, "ai_estimate": 0}
    for plan in planned:
        source = plan["source"]
        entry = plan["entry"]
        tier = plan["tier"] or "ai_estimate"
        counts[tier] += 1
        if entry is not None:
            unit_price = _q6(entry.unit_price) or Decimal("0")
            reference = _matched_reference(
                source,
                entry,
                tier=tier,
                match=plan["match"],
                enterprise_meta=enterprise_meta,
            )
            source_label = "账户定额" if tier == "account_quota" else "企业定额"
            basis = f"按“账户定额 → 企业定额 → AI估价”顺序命中{source_label} active 条目。"
            notes = construction_note_only(source["remark"])
            estimate: dict[str, Any] = {}
        else:
            estimate = ai_results.get(source["row_key"]) or _rule_estimate(
                {
                    "item_name": source["item_name"],
                    "spec": source["spec"],
                    "unit": source["unit"],
                    "normalized_unit": source["normalized_unit"],
                },
                fallback_reason="missing_batch_result",
            )
            unit_price = _q6(_decimal(estimate.get("unit_price"))) or Decimal("0")
            reference = _no_match_reference(
                source,
                unit_price=unit_price,
                attempts=plan["attempts"],
            )
            basis = str(estimate.get("basis") or "账户定额与企业定额均未命中，使用 AI 估价。")
            notes = construction_note_only(source["remark"])

        line_total = _total(source["quantity"], unit_price)
        reference["pricing_match_attempts"] = plan["attempts"]
        preview_row = {
                "requirement_row_key": source["row_key"],
                "source_sheet": source["source_sheet"],
                "raw_row_index": source["raw_row_index"],
                "project_name": source["item_name"],
                "item_name": source["item_name"],
                "spec": source["spec"],
                "quantity": float(source["quantity"]) if source["quantity"] is not None else 0,
                "unit": source["unit"],
                "unit_price": float(unit_price),
                "total_price": float(line_total),
                "ai_suggested_unit_price": float(unit_price),
                "ai_suggested_total_price": float(line_total),
                "manual_unit_price": float(unit_price),
                "manual_price_source": "pricing_cascade_prefill",
                "manual_price_action": "untouched",
                "manual_total_source": "pricing_cascade_prefill",
                "price_confirmed_by_user": False,
                "notes": notes,
                "raw_text": source["raw_text"],
                "pricing_tier": tier,
                "price_source": tier,
                "pricing_source_snapshot": entry.snapshot if entry is not None else estimate,
                "pricing_match_attempts": plan["attempts"],
                "standardization": source["standardization"],
                "standardization_warnings": source["standardization"]["warnings"],
                "cost_reference": reference,
                "quote_explanation": {
                    "ai_price_source": f"{tier}_adopted" if tier != "ai_estimate" else "model_estimate",
                    "ai_price_source_label": (
                        "账户定额" if tier == "account_quota" else "企业定额" if tier == "enterprise_quota" else "AI估价"
                    ),
                    "ai_price_source_reason": basis,
                    "ai_basis": basis,
                    "cost_context_basis": (
                        "本行已按账户定额优先、企业定额其次的顺序完成匹配。"
                        if tier != "ai_estimate"
                        else "账户定额与企业定额均未命中，AI仅对本行进行估价。"
                    ),
                    "comparison": "请在预审中核对工程量、单位、价格来源和最终合计。",
                    "pricing_engine_version": CASCADE_ENGINE_VERSION,
                },
                "ai_estimate": estimate if tier == "ai_estimate" else None,
            }
        if plan["enterprise_v2_review"] is not None:
            preview_row["enterprise_quota_v2_review"] = plan["enterprise_v2_review"]
        preview_rows.append(preview_row)

    total_price = round(sum(float(row["total_price"] or 0) for row in preview_rows), 2)
    v2_reviews = [
        plan["enterprise_v2_review"]
        for plan in planned
        if isinstance(plan.get("enterprise_v2_review"), dict)
    ]
    return {
        "project_details": preview_rows,
        "total_price": total_price,
        "pricing_cascade_summary": {
            "engine_version": CASCADE_ENGINE_VERSION,
            "priority": list(CASCADE_PRIORITY),
            "row_count": len(preview_rows),
            "account_quota_matched_count": counts["account_quota"],
            "enterprise_quota_matched_count": counts["enterprise_quota"],
            "ai_estimate_count": counts["ai_estimate"],
            "enterprise_quota_v2_review_enabled": bool(settings.feature_enterprise_quota_v2_review),
            "enterprise_quota_v2_candidate_count": sum(
                1 for review in v2_reviews if int(review.get("candidate_count") or 0) > 0
            ),
            "enterprise_quota_v2_auto_adopted_count": sum(
                1 for review in v2_reviews if review.get("manual_confirmation_status") == "auto_adopted"
            ),
            "enterprise_quota_v2_high_confidence_count": sum(
                1 for review in v2_reviews if review.get("decision") == SHADOW_DECISION_AUTO
            ),
            "enterprise_quota_v2_manual_review_count": sum(
                1 for review in v2_reviews if review.get("decision") == SHADOW_DECISION_REVIEW
            ),
            "enterprise_quota_v2_no_candidate_count": sum(
                1 for review in v2_reviews if review.get("decision") == SHADOW_DECISION_NONE
            ),
            "enterprise_quota_v2_pending_confirmation_count": sum(
                1
                for review in v2_reviews
                if review.get("requires_manual_confirmation")
                and review.get("manual_confirmation_status") == "pending"
            ),
            "account_catalog": account_meta,
            "enterprise_catalog": enterprise_meta,
        },
        "standardization_summary": standardization_summary or {},
        "quote_explanation": {
            "pricing_priority": "账户定额 → 企业定额 → AI估价",
            "manual_review_required": True,
        },
    }
