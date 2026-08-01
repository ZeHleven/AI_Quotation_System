"""Persist one chat quote as the editable draft of one budget project.

The legacy chat quote and the budget-project workbench historically produced
two independent drafts.  This bridge keeps the chat as the intake surface, but
materializes its latest preview draft into the existing budget project,
formal-import and pricing-draft aggregates before the user opens the detailed
workbench.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account_quota import AccountQuotaItem
from app.models.budget_project import (
    BUDGET_IMPORT_STATUS_ACTIVE,
    BudgetProjectImportBatch,
    BudgetProjectProfile,
)
from app.models.budget_pricing_draft import (
    PRICING_MODE_ENTERPRISE_AI,
    BudgetProjectPricingDraft,
    BudgetProjectPricingDraftLine,
)
from app.models.enterprise_quota import EnterpriseQuotaItem
from app.models.file_object import FileObject
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.budget_pricing import BudgetPricingError, _decimal, _json_dump, _json_load, _q6
from app.services.budget_pricing_drafts import (
    _append_event,
    _apply_effective_price,
    _fallback_breakdown_from_price,
    create_or_rebuild_budget_pricing_draft,
    get_current_budget_pricing_draft,
    refresh_budget_pricing_draft_summary,
)
from app.services.budget_projects import (
    BUDGET_WORKSPACE_ACTIVE,
    activate_import_batch,
    confirm_import_batch,
    create_budget_project,
    create_import_batch,
    get_budget_profile,
)
from app.services.construction_notes import construction_note_only
from app.services.quote_history import parse_amount, project_details
from app.services.quote_preview_drafts import get_preview_draft


BRIDGE_VERSION = "chat-budget-workspace-bridge-v1"
_SUPPORTED_WORKBOOK_SUFFIXES = {".xlsx", ".xlsm"}
_BREAKDOWN_KEYS = (
    "labor_unit_cost",
    "main_material_unit_cost",
    "auxiliary_material_unit_cost",
    "machinery_unit_cost",
    "comprehensive_unit_cost",
    "management_unit_cost",
    "profit_unit_cost",
    "measure_unit_cost",
    "subcontract_unit_cost",
    "tax_amount",
    "main_material_without_loss",
    "loss_rate",
    "owner_material_unit_price",
    "owner_material_loss_amount",
    "material_supply_mode",
)
_ZERO_TOTALS_CONFIG = {
    "measures_rate": "0.000000",
    "management_rate": "0.000000",
    "other_fee": "0.000000",
    "suspended_amount": "0.000000",
    "area": "0.000000",
    "quote_adjustment_percent": "0.000000",
}


class QuoteBudgetWorkspaceError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409, context: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.context = context or {}

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


def _json_payload(raw_value: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw_value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _quote_payload(db: Session, job: QuoteJob) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    saved = get_preview_draft(db, job.job_id)
    saved_payload = saved.get("draft") if saved.get("exists") and saved.get("status") != "discarded" else None
    payload = saved_payload if isinstance(saved_payload, dict) else _json_payload(job.result_json)
    rows = project_details(payload)
    if not rows:
        raise QuoteBudgetWorkspaceError("QUOTE_BUDGET_WORKSPACE_ROWS_REQUIRED", status_code=422)
    return payload, rows


def _clean_title(value: Any, limit: int = 255) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ._-")
    return text[:limit]


def _project_title(job: QuoteJob, rows: list[dict[str, Any]]) -> str:
    filename = job.source_file_name or job.file_name
    if filename:
        title = _clean_title(Path(filename).stem)
        if title:
            return title
    if job.request_summary:
        title = _clean_title(job.request_summary, 120)
        if title:
            return title
    first_name = _clean_title(rows[0].get("item_name") or rows[0].get("project_name"), 100)
    return f"{first_name or '新建'}报价项目"


def _synthetic_workbook(rows: list[dict[str, Any]], *, title: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "标准清单"
    sheet.append(["序号", "项目名称", "特征描述", "工程量", "单位", "备注"])
    for index, row in enumerate(rows, start=1):
        quantity = parse_amount(row.get("quantity") or row.get("calculation_quantity"))
        sheet.append(
            [
                index,
                _clean_title(row.get("item_name") or row.get("project_name"), 255),
                str(row.get("spec") or row.get("project_feature") or "").strip(),
                quantity if quantity is not None else None,
                str(row.get("unit") or "").strip(),
                str(row.get("notes") or row.get("remark") or "").strip(),
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.column_dimensions["B"].width = 30
    sheet.column_dimensions["C"].width = 56
    sheet.column_dimensions["F"].width = 56
    workbook.properties.title = title
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _workbook_source(
    job: QuoteJob,
    rows: list[dict[str, Any]],
    *,
    file_content: bytes | None,
) -> tuple[bytes, str, FileObject | None, bool]:
    filename = next(
        (
            candidate
            for candidate in (job.source_file_name, job.file_name)
            if candidate and Path(candidate).suffix.lower() in _SUPPORTED_WORKBOOK_SUFFIXES
        ),
        job.source_file_name or job.file_name or "",
    )
    suffix = Path(filename).suffix.lower()
    if file_content and suffix in _SUPPORTED_WORKBOOK_SUFFIXES:
        source_file = None
        return file_content, filename, source_file, True
    title = _project_title(job, rows)
    return _synthetic_workbook(rows, title=title), f"{title}_标准清单.xlsx", None, False


def _source_file_object(db: Session, job: QuoteJob, *, original_workbook: bool) -> FileObject | None:
    if not original_workbook or not job.file_object_id:
        return None
    return db.query(FileObject).filter(FileObject.file_id == job.file_object_id).first()


def _accessible_profile(db: Session, project_id: int, current_user: User) -> BudgetProjectProfile | None:
    try:
        return get_budget_profile(db, project_id, current_user)
    except HTTPException:
        return None


def _find_reusable_active_import(
    db: Session,
    *,
    source_sha256: str,
    current_user: User,
) -> tuple[BudgetProjectProfile, BudgetProjectImportBatch] | None:
    candidates = (
        db.query(BudgetProjectImportBatch)
        .filter(
            BudgetProjectImportBatch.source_file_sha256 == source_sha256,
            BudgetProjectImportBatch.status == BUDGET_IMPORT_STATUS_ACTIVE,
        )
        .order_by(BudgetProjectImportBatch.id.desc())
        .limit(20)
        .all()
    )
    for batch in candidates:
        profile = _accessible_profile(db, int(batch.project_id), current_user)
        if (
            profile is not None
            and profile.workspace_status == BUDGET_WORKSPACE_ACTIVE
            and int(profile.active_import_batch_id or 0) == int(batch.id)
            and int(profile.active_import_revision_id or 0) == int(batch.confirmed_revision_id or 0)
        ):
            return profile, batch
    return None


def _active_batch(db: Session, profile: BudgetProjectProfile) -> BudgetProjectImportBatch | None:
    if not profile.active_import_batch_id:
        return None
    batch = db.query(BudgetProjectImportBatch).filter(
        BudgetProjectImportBatch.id == profile.active_import_batch_id
    ).first()
    if (
        batch is None
        or batch.status != BUDGET_IMPORT_STATUS_ACTIVE
        or int(batch.project_id) != int(profile.project_id)
        or int(batch.confirmed_revision_id or 0) != int(profile.active_import_revision_id or 0)
    ):
        return None
    return batch


def _ensure_project_and_import(
    db: Session,
    job: QuoteJob,
    current_user: User,
    *,
    rows: list[dict[str, Any]],
    workbook_content: bytes,
    workbook_filename: str,
    source_file_object: FileObject | None,
) -> tuple[BudgetProjectProfile, BudgetProjectImportBatch, bool]:
    if job.budget_project_id:
        profile = _accessible_profile(db, int(job.budget_project_id), current_user)
        if profile is None:
            raise QuoteBudgetWorkspaceError(
                "QUOTE_BUDGET_WORKSPACE_PROJECT_NOT_ACCESSIBLE",
                status_code=404,
                context={"budget_project_id": job.budget_project_id},
            )
        batch = _active_batch(db, profile)
        if batch is not None:
            return profile, batch, True

    source_sha256 = hashlib.sha256(workbook_content).hexdigest()
    reusable = _find_reusable_active_import(
        db,
        source_sha256=source_sha256,
        current_user=current_user,
    )
    if reusable is not None:
        return reusable[0], reusable[1], True

    profile = create_budget_project(
        db,
        {
            "name": _project_title(job, rows),
            "description": f"由对话报价任务 {job.job_id} 自动建立；详细报价与对话摘要共用同一份草稿。",
        },
        current_user,
    )
    batch = create_import_batch(
        db,
        profile,
        filename=workbook_filename,
        content=workbook_content,
        current_user=current_user,
        source_file_object=source_file_object,
    )
    batch = confirm_import_batch(db, profile, batch, current_user)
    batch = activate_import_batch(db, profile, batch, current_user)
    return profile, batch, False


def _source_hash(
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    canonical = {
        "bridge_version": BRIDGE_VERSION,
        "rows": rows,
        "total_price": parse_amount(payload.get("total_price") or payload.get("total_amount")),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _row_key(row: dict[str, Any]) -> str:
    return str(row.get("requirement_row_key") or row.get("source_row_key") or "").strip()


def _row_name(row: dict[str, Any]) -> str:
    return re.sub(r"\s+", "", str(row.get("item_name") or row.get("project_name") or "")).lower()


def _align_rows(
    draft_lines: list[BudgetProjectPricingDraftLine],
    quote_rows: list[dict[str, Any]],
) -> list[tuple[BudgetProjectPricingDraftLine, dict[str, Any]]]:
    by_key = {line.source_row_key: line for line in draft_lines}
    unused = {line.id: line for line in draft_lines}
    aligned: list[tuple[BudgetProjectPricingDraftLine, dict[str, Any]]] = []
    for index, row in enumerate(quote_rows):
        line = by_key.get(_row_key(row))
        if line is None:
            name = _row_name(row)
            line = next(
                (
                    candidate
                    for candidate in unused.values()
                    if _row_name({"item_name": candidate.item_name}) == name
                ),
                None,
            )
        if line is None and index < len(draft_lines):
            candidate = draft_lines[index]
            if candidate.id in unused:
                line = candidate
        if line is None or line.id not in unused:
            continue
        unused.pop(line.id, None)
        aligned.append((line, row))
    if len(aligned) != len(quote_rows) or len(aligned) != len(draft_lines):
        raise QuoteBudgetWorkspaceError(
            "QUOTE_BUDGET_WORKSPACE_ROW_MISMATCH",
            context={
                "quote_row_count": len(quote_rows),
                "draft_line_count": len(draft_lines),
                "matched_row_count": len(aligned),
            },
        )
    return aligned


def _positive_decimal(value: Any) -> Decimal | None:
    amount = parse_amount(value)
    if amount is None:
        return None
    try:
        parsed = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return _q6(parsed) if parsed.is_finite() and parsed > 0 else None


def _pricing_tier(row: dict[str, Any]) -> str:
    tier = str(row.get("pricing_tier") or row.get("price_source") or "").strip().lower()
    if tier in {"account", "account_quota"}:
        return "account_quota"
    if tier in {"enterprise", "enterprise_quota"}:
        return "enterprise_quota"
    return "ai_estimate"


def _valid_account_quota_id(db: Session, row: dict[str, Any]) -> int | None:
    reference = row.get("cost_reference") if isinstance(row.get("cost_reference"), dict) else {}
    candidate = reference.get("account_quota_item_id")
    if not candidate:
        return None
    try:
        candidate_id = int(candidate)
    except (TypeError, ValueError):
        return None
    return candidate_id if db.query(AccountQuotaItem.id).filter(AccountQuotaItem.id == candidate_id).first() else None


def _valid_enterprise_quota_id(db: Session, row: dict[str, Any]) -> int | None:
    reference = row.get("cost_reference") if isinstance(row.get("cost_reference"), dict) else {}
    candidate = reference.get("enterprise_quota_item_id")
    if not candidate:
        return None
    try:
        candidate_id = int(candidate)
    except (TypeError, ValueError):
        return None
    return candidate_id if db.query(EnterpriseQuotaItem.id).filter(EnterpriseQuotaItem.id == candidate_id).first() else None


def _pricing_breakdown(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("pricing_breakdown", "cost_breakdown", "price_breakdown"):
        value = row.get(key)
        if isinstance(value, dict) and value:
            breakdown = dict(value)
            break
    else:
        breakdown = {
            key: row.get(key)
            for key in _BREAKDOWN_KEYS
            if row.get(key) not in (None, "")
        }
    reference = row.get("cost_reference") if isinstance(row.get("cost_reference"), dict) else {}
    explanation = row.get("quote_explanation") if isinstance(row.get("quote_explanation"), dict) else {}
    estimate = row.get("ai_estimate") if isinstance(row.get("ai_estimate"), dict) else {}
    nested_estimate = estimate.get("estimate") if isinstance(estimate.get("estimate"), dict) else {}
    pricing_phrases = (
        estimate.get("basis"),
        nested_estimate.get("basis"),
        explanation.get("ai_basis"),
        explanation.get("ai_price_source_reason"),
        explanation.get("cost_context_basis"),
        reference.get("ai_price_source_reason"),
        reference.get("price_source_reason"),
        reference.get("message"),
    )
    notes = construction_note_only(
        row.get("notes") or row.get("remark"),
        pricing_phrases=pricing_phrases,
    )
    if notes:
        breakdown["remark"] = notes[:2000]
    breakdown["source"] = BRIDGE_VERSION
    return breakdown


def _manual_override(row: dict[str, Any]) -> bool:
    action = str(row.get("manual_price_action") or "untouched").strip().lower()
    return action in {"manual_override", "manual_existing"}


def _sync_line(
    db: Session,
    line: BudgetProjectPricingDraftLine,
    row: dict[str, Any],
    *,
    current_user: User,
    quote_job_id: str,
) -> str:
    item_name = str(row.get("item_name") or row.get("project_name") or "").strip()
    spec = str(row.get("spec") or row.get("project_feature") or "").strip()
    unit = str(row.get("unit") or "").strip()
    raw_quantity = row.get("quantity")
    if raw_quantity in (None, ""):
        raw_quantity = row.get("calculation_quantity")
    quantity_value = parse_amount(raw_quantity)
    quantity = _q6(Decimal(str(quantity_value))) if quantity_value is not None else None
    if item_name:
        line.item_name = item_name[:255]
    line.spec = spec or None
    line.unit = unit[:64] or None
    if quantity is not None and quantity > 0:
        line.calculation_quantity = quantity
        line.quantity_status = "valid"
    else:
        line.calculation_quantity = Decimal("0.000000")
        unresolved_status = str(row.get("quantity_status") or "missing").strip()[:32]
        line.quantity_status = unresolved_status if unresolved_status and unresolved_status != "valid" else "missing"

    tier = _pricing_tier(row)
    effective = _positive_decimal(
        row.get("confirmed_unit_price")
        or row.get("final_unit_price")
        or row.get("manual_unit_price")
        or row.get("unit_price")
        or row.get("price")
    )
    suggested = _positive_decimal(
        row.get("ai_suggested_unit_price")
        or row.get("unit_price")
        or row.get("price")
    )
    reference = row.get("cost_reference") if isinstance(row.get("cost_reference"), dict) else {}
    account_quota_id = _valid_account_quota_id(db, row) if tier == "account_quota" else None
    enterprise_quota_id = _valid_enterprise_quota_id(db, row) if tier == "enterprise_quota" else None
    line.selected_account_quota_item_id = account_quota_id
    line.selected_enterprise_quota_item_id = enterprise_quota_id
    line.selected_source_snapshot_json = _json_dump(
        reference.get("source_cost_item")
        or row.get("pricing_source_snapshot")
        or row.get("ai_estimate")
        or {}
    )
    line.match_status = "auto_matched" if tier in {"account_quota", "enterprise_quota"} else "unmatched"
    line.match_evidence_json = _json_dump(
        {
            "bridge_version": BRIDGE_VERSION,
            "source_quote_job_id": quote_job_id,
            "base_price_source": tier if tier in {"account_quota", "enterprise_quota"} else "none",
            "pricing_tier": tier,
            "pricing_match_attempts": row.get("pricing_match_attempts") or [],
            "cost_reference": reference,
        }
    )
    line.base_unit_price = suggested if tier in {"account_quota", "enterprise_quota"} else None
    line.ai_estimated_unit_price = suggested if tier == "ai_estimate" else None
    line.ai_estimate_snapshot_json = _json_dump(row.get("ai_estimate") or row.get("pricing_source_snapshot") or {}) if tier == "ai_estimate" else None
    line.manual_unit_price = None
    _apply_effective_price(
        line,
        manual_unit_price=effective if _manual_override(row) else None,
    )
    if effective is not None and not _manual_override(row):
        if tier in {"account_quota", "enterprise_quota"}:
            line.base_unit_price = effective
        else:
            line.ai_estimated_unit_price = effective
        _apply_effective_price(line, manual_unit_price=None)
    explicit_total = _positive_decimal(
        row.get("confirmed_total_price")
        or row.get("final_total_price")
        or row.get("total_price")
        or row.get("amount")
    )
    if explicit_total is not None and line.amount_included:
        line.line_total = explicit_total
    breakdown = _pricing_breakdown(row)
    has_component_values = any(
        _decimal(breakdown.get(key)) not in (None, Decimal("0"))
        for key in _BREAKDOWN_KEYS
        if key not in {"material_supply_mode"}
    )
    if effective is not None and not has_component_values:
        derived = _fallback_breakdown_from_price(line, effective)
        derived.update(breakdown)
        breakdown = derived
    line.pricing_breakdown_json = _json_dump(breakdown)
    warnings = list(row.get("standardization_warnings") or row.get("warnings") or [])
    line.warnings_json = _json_dump(warnings)
    line.line_revision = int(line.line_revision or 0) + 1
    line.updated_by = current_user.id
    return line.price_source


def _reset_totals_config(draft: BudgetProjectPricingDraft) -> None:
    summary = _json_load(draft.summary_json, {})
    if not isinstance(summary, dict):
        summary = {}
    summary["totals_config"] = dict(_ZERO_TOTALS_CONFIG)
    draft.summary_json = _json_dump(summary)


def _sync_pricing_draft(
    db: Session,
    profile: BudgetProjectProfile,
    batch: BudgetProjectImportBatch,
    current_user: User,
    *,
    job: QuoteJob,
    rows: list[dict[str, Any]],
) -> tuple[BudgetProjectPricingDraft, dict[str, Any]]:
    current = get_current_budget_pricing_draft(
        db,
        profile,
        current_user,
        pricing_mode=PRICING_MODE_ENTERPRISE_AI,
        for_update=True,
    )
    try:
        draft = create_or_rebuild_budget_pricing_draft(
            db,
            profile,
            current_user,
            pricing_mode=PRICING_MODE_ENTERPRISE_AI,
            source_import_batch_id=batch.id,
            source_import_revision_id=int(batch.confirmed_revision_id or 0),
            expected_revision=int(current.revision) if current is not None else None,
            reason=f"chat_quote_job:{job.job_id}",
        )
    except BudgetPricingError as exc:
        raise QuoteBudgetWorkspaceError(
            exc.code,
            status_code=getattr(exc, "status_code", 409),
            context=getattr(exc, "context", None) or {},
        ) from exc

    draft_lines = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
        .order_by(BudgetProjectPricingDraftLine.source_sort_order, BudgetProjectPricingDraftLine.id)
        .with_for_update()
        .all()
    )
    aligned = _align_rows(draft_lines, rows)
    previous_revision = int(draft.revision)
    counts = {"account_quota": 0, "enterprise_quota": 0, "ai_estimate": 0, "manual": 0}
    for line, row in aligned:
        price_source = _sync_line(
            db,
            line,
            row,
            current_user=current_user,
            quote_job_id=job.job_id,
        )
        tier = _pricing_tier(row)
        counts[tier] += 1
        if price_source == "manual":
            counts["manual"] += 1

    _reset_totals_config(draft)
    draft.revision = previous_revision + 1
    draft.updated_by = current_user.id
    summary = refresh_budget_pricing_draft_summary(db, draft)
    summary.update(
        {
            "source_quote_job_id": job.job_id,
            "bridge_version": BRIDGE_VERSION,
            "account_quota_matched_count": counts["account_quota"],
            "enterprise_quota_matched_count": counts["enterprise_quota"],
            "ai_estimate_count": counts["ai_estimate"],
            "manual_override_count": counts["manual"],
        }
    )
    draft.summary_json = _json_dump(summary)
    _append_event(
        db,
        draft=draft,
        current_user=current_user,
        event_type="chat_quote_imported",
        from_mode=draft.pricing_mode,
        from_revision=previous_revision,
        event={
            "bridge_version": BRIDGE_VERSION,
            "quote_job_id": job.job_id,
            "row_count": len(rows),
            "pricing_source_counts": counts,
        },
    )
    db.flush()
    return draft, summary


def _existing_link_result(
    db: Session,
    job: QuoteJob,
    current_user: User,
    *,
    source_sha256: str,
) -> dict[str, Any] | None:
    if (
        not job.budget_project_id
        or not job.budget_pricing_draft_id
        or job.budget_workspace_source_sha256 != source_sha256
    ):
        return None
    profile = _accessible_profile(db, int(job.budget_project_id), current_user)
    draft = db.query(BudgetProjectPricingDraft).filter(
        BudgetProjectPricingDraft.id == job.budget_pricing_draft_id,
        BudgetProjectPricingDraft.project_id == job.budget_project_id,
    ).first()
    if profile is None or profile.workspace_status != BUDGET_WORKSPACE_ACTIVE or draft is None:
        return None
    return {
        "budget_project_id": int(profile.project_id),
        "budget_pricing_draft_id": int(draft.id),
        "budget_pricing_draft_uuid": draft.draft_uuid,
        "pricing_mode": draft.pricing_mode,
        "detail_url": f"/admin/budget-projects/{profile.project_id}",
        "reused_project": True,
        "synced": False,
        "source_sha256": source_sha256,
        "row_count": int(draft.row_count or 0),
        "quote_amount": str(draft.priced_subtotal or 0),
    }


def materialize_quote_budget_workspace(
    db: Session,
    *,
    job: QuoteJob,
    current_user: User,
    file_content: bytes | None,
) -> dict[str, Any]:
    if not settings.feature_budget_projects or not settings.feature_budget_pricing_drafts:
        raise QuoteBudgetWorkspaceError("QUOTE_BUDGET_WORKSPACE_FEATURE_DISABLED", status_code=404)
    # Serialize concurrent clicks so one quote task cannot create two projects
    # or rebuild the same pricing draft twice.
    job = (
        db.query(QuoteJob)
        .filter(QuoteJob.id == job.id)
        .with_for_update()
        .one()
    )
    if job.status != "succeeded" or not job.result_json:
        raise QuoteBudgetWorkspaceError(
            "QUOTE_BUDGET_WORKSPACE_JOB_NOT_READY",
            context={"quote_job_status": job.status},
        )

    payload, rows = _quote_payload(db, job)
    payload_sha256 = _source_hash(payload, rows)
    existing = _existing_link_result(
        db,
        job,
        current_user,
        source_sha256=payload_sha256,
    )
    if existing is not None:
        return existing

    workbook_content, workbook_filename, _, original_workbook = _workbook_source(
        job,
        rows,
        file_content=file_content,
    )
    source_file_object = _source_file_object(db, job, original_workbook=original_workbook)
    profile, batch, reused_project = _ensure_project_and_import(
        db,
        job,
        current_user,
        rows=rows,
        workbook_content=workbook_content,
        workbook_filename=workbook_filename,
        source_file_object=source_file_object,
    )
    draft, summary = _sync_pricing_draft(
        db,
        profile,
        batch,
        current_user,
        job=job,
        rows=rows,
    )

    job.budget_project_id = int(profile.project_id)
    job.budget_pricing_draft_id = int(draft.id)
    job.budget_workspace_source_sha256 = payload_sha256
    job.budget_workspace_synced_at = datetime.now(timezone.utc)
    result_payload = _json_payload(job.result_json)
    synchronized_total = round(
        sum(
            parse_amount(
                row.get("confirmed_total_price")
                or row.get("final_total_price")
                or row.get("total_price")
                or row.get("amount")
            )
            or 0
            for row in rows
        ),
        2,
    )
    result_payload.update(
        {
            "project_details": rows,
            "total_price": synchronized_total,
            "budget_project_id": int(profile.project_id),
            "budget_pricing_draft_id": int(draft.id),
            "budget_pricing_draft_uuid": draft.draft_uuid,
            "budget_pricing_mode": draft.pricing_mode,
        }
    )
    job.result_json = json.dumps(result_payload, ensure_ascii=False)
    job.result_item_count = len(rows)
    job.result_total_amount = synchronized_total
    db.flush()
    return {
        "budget_project_id": int(profile.project_id),
        "budget_pricing_draft_id": int(draft.id),
        "budget_pricing_draft_uuid": draft.draft_uuid,
        "pricing_mode": draft.pricing_mode,
        "detail_url": f"/admin/budget-projects/{profile.project_id}",
        "reused_project": reused_project,
        "synced": True,
        "source_sha256": payload_sha256,
        "row_count": len(rows),
        "quote_amount": (summary.get("totals") or {}).get("quote_amount"),
    }
