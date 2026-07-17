"""Account-scoped mutable dual-mode budget pricing drafts (P2-2A).

This module deliberately does not call ``create_budget_pricing_run`` and does
not import the legacy quote/RAG price chain.  ``enterprise_ai`` reuses only the
P2-1 enterprise-quota matcher; its LLM fallback is explicitly not connected.
``account_strict`` reads only current-account ``AccountQuotaItem.active`` rows
and never falls back to enterprise quota data or automatic LLM estimation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.account_quota import ACCOUNT_QUOTA_STATUS_ACTIVE, AccountQuotaItem
from app.models.budget_project import BudgetProjectProfile
from app.models.budget_pricing import (
    PRICING_LINE_STATUS_PENDING_MATCH,
    PRICING_LINE_STATUS_PRICED,
    PRICING_LINE_STATUS_QUANTITY_UNRESOLVED,
    PRICING_LINE_STATUS_UNIT_CONFLICT,
    PRICING_MATCH_UNIT_CONFLICT,
)
from app.models.budget_pricing_draft import (
    PRICING_DRAFT_STATUS_ACTIVE,
    PRICING_MODE_ACCOUNT_STRICT,
    PRICING_MODE_ENTERPRISE_AI,
    BudgetProjectPricingDraft,
    BudgetProjectPricingDraftEvent,
    BudgetProjectPricingDraftLine,
)
from app.models.user import User
from app.services.account_tenancy import require_budget_project_account
from app.services.budget_pricing import (
    MATCHING_ENGINE_VERSION,
    PRICING_ENGINE_VERSION,
    BudgetPricingError,
    _NUMERIC_20_6_MAX,
    _NUMERIC_24_6_MAX,
    _QuotaEntry,
    _build_catalog_index,
    _decimal,
    _decimal_text,
    _fits_numeric,
    _format_dt,
    _json_dump,
    _json_load,
    _load_quota_catalog,
    _match_source,
    _normalize_text,
    _pricing_values,
    _q6,
    _resolve_formal_source,
    _sha256,
    _source_values,
    normalize_pricing_unit,
    strict_active_quota_version,
)


SUPPORTED_PRICING_MODES = {PRICING_MODE_ENTERPRISE_AI, PRICING_MODE_ACCOUNT_STRICT}
ACCOUNT_STRICT_ENGINE_VERSION = "account-quota-strict-p2-2b3"


def _mode_guard(pricing_mode: str) -> str:
    mode = str(pricing_mode or "").strip()
    if mode not in SUPPORTED_PRICING_MODES:
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_MODE_INVALID",
            status_code=422,
            context={"pricing_mode": mode},
        )
    return mode


def _locked_profile(db: Session, profile: BudgetProjectProfile) -> BudgetProjectProfile:
    locked = (
        db.query(BudgetProjectProfile)
        .filter(BudgetProjectProfile.id == profile.id)
        .with_for_update()
        .one_or_none()
    )
    if locked is None or int(locked.project_id) != int(profile.project_id):
        raise BudgetPricingError("BUDGET_PROJECT_NOT_FOUND", status_code=404)
    if locked.workspace_status != "active":
        raise BudgetPricingError("BUDGET_PROJECT_ARCHIVED")
    return locked


def _current_draft_query(db: Session, *, account_id: int, project_id: int):
    return db.query(BudgetProjectPricingDraft).filter(
        BudgetProjectPricingDraft.account_id == account_id,
        BudgetProjectPricingDraft.project_id == project_id,
        BudgetProjectPricingDraft.status == PRICING_DRAFT_STATUS_ACTIVE,
    )


def get_current_budget_pricing_draft(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    for_update: bool = False,
) -> BudgetProjectPricingDraft | None:
    account, _ = require_budget_project_account(
        db,
        project_id=profile.project_id,
        current_user=current_user,
        for_update=for_update,
    )
    query = _current_draft_query(db, account_id=account.id, project_id=profile.project_id)
    if for_update:
        query = query.with_for_update()
    return query.one_or_none()


def _append_event(
    db: Session,
    *,
    draft: BudgetProjectPricingDraft,
    current_user: User,
    event_type: str,
    from_mode: str | None,
    from_revision: int | None,
    event: dict[str, Any] | None = None,
) -> None:
    db.add(
        BudgetProjectPricingDraftEvent(
            event_uuid=str(uuid4()),
            draft_id=draft.id,
            account_id=draft.account_id,
            project_id=draft.project_id,
            event_type=event_type,
            from_mode=from_mode,
            to_mode=draft.pricing_mode,
            from_revision=from_revision,
            to_revision=draft.revision,
            actor_id=current_user.id,
            event_json=_json_dump(event or {}),
        )
    )


def _line_base_status(line: BudgetProjectPricingDraftLine) -> str:
    evidence = _json_load(line.match_evidence_json, {})
    stored = evidence.get("base_pricing_status") if isinstance(evidence, dict) else None
    if stored:
        return str(stored)
    if line.base_unit_price is None:
        return (
            PRICING_LINE_STATUS_UNIT_CONFLICT
            if line.match_status == PRICING_MATCH_UNIT_CONFLICT
            else PRICING_LINE_STATUS_PENDING_MATCH
        )
    if line.quantity_status == "valid" and Decimal(line.calculation_quantity or 0) > 0:
        return PRICING_LINE_STATUS_PRICED
    return PRICING_LINE_STATUS_QUANTITY_UNRESOLVED


def _apply_effective_price(
    line: BudgetProjectPricingDraftLine,
    *,
    manual_unit_price: Decimal | None,
) -> None:
    manual = _q6(manual_unit_price)
    ai_estimate = _q6(_decimal(line.ai_estimated_unit_price))
    base = _q6(_decimal(line.base_unit_price))
    effective = manual if manual is not None else (ai_estimate if ai_estimate is not None else base)
    if effective is not None and (
        effective <= 0 or not _fits_numeric(effective, _NUMERIC_20_6_MAX)
    ):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_MANUAL_PRICE_INVALID",
            status_code=422,
        )
    line.manual_unit_price = manual
    line.effective_unit_price = effective
    evidence = _json_load(line.match_evidence_json, {})
    base_source = evidence.get("base_price_source") if isinstance(evidence, dict) else None
    if base_source not in {"enterprise_quota", "account_quota"}:
        base_source = "enterprise_quota"
    if manual is not None:
        line.price_source = "manual"
    elif ai_estimate is not None:
        line.price_source = "ai_estimate"
    else:
        line.price_source = base_source if base is not None else "none"
    if effective is None:
        line.line_total = None
        line.amount_included = False
        line.pricing_status = _line_base_status(line)
        return
    quantity = _q6(_decimal(line.calculation_quantity)) or Decimal("0.000000")
    if line.quantity_status != "valid" or quantity <= 0:
        # Unit price is still useful, but an unresolved quantity contributes
        # zero to the visible subtotal and remains excluded from completeness.
        line.line_total = Decimal("0.000000")
        line.amount_included = False
        line.pricing_status = PRICING_LINE_STATUS_QUANTITY_UNRESOLVED
        return
    total = quantity * effective
    if not _fits_numeric(total, _NUMERIC_24_6_MAX):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_LINE_TOTAL_OVERFLOW",
            status_code=422,
        )
    line.line_total = _q6(total)
    line.amount_included = True
    line.pricing_status = PRICING_LINE_STATUS_PRICED


def _refresh_summary(db: Session, draft: BudgetProjectPricingDraft) -> dict[str, Any]:
    db.flush()
    lines = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
        .order_by(BudgetProjectPricingDraftLine.source_sort_order, BudgetProjectPricingDraftLine.id)
        .all()
    )
    row_count = len(lines)
    matched_count = sum(
        line.selected_enterprise_quota_item_id is not None
        or line.selected_account_quota_item_id is not None
        for line in lines
    )
    priced_count = sum(line.effective_unit_price is not None for line in lines)
    amount_priced_count = sum(bool(line.amount_included) for line in lines)
    pending_count = sum(line.effective_unit_price is None for line in lines)
    manual_price_count = sum(line.manual_unit_price is not None for line in lines)
    ai_estimate_count = sum(line.ai_estimated_unit_price is not None for line in lines)
    quantity_unresolved_count = sum(
        line.quantity_status != "valid" or Decimal(line.calculation_quantity or 0) <= 0
        for line in lines
    )
    subtotal = Decimal("0")
    for line in lines:
        if line.amount_included and line.line_total is not None:
            subtotal += Decimal(line.line_total)
    subtotal = _q6(subtotal) or Decimal("0.000000")
    complete = bool(
        row_count
        and pending_count == 0
        and quantity_unresolved_count == 0
        and amount_priced_count == row_count
    )
    completeness = "complete" if complete else "partial"
    summary = {
        "pricing_mode": draft.pricing_mode,
        "row_count": row_count,
        "standard_item_count": row_count,
        "matched_count": matched_count,
        "unit_priced_count": priced_count,
        "amount_priced_count": amount_priced_count,
        "pending_count": pending_count,
        "manual_price_count": manual_price_count,
        "ai_estimate_count": ai_estimate_count,
        "quantity_unresolved_count": quantity_unresolved_count,
        "priced_subtotal": _decimal_text(subtotal),
        "total_cost": _decimal_text(subtotal) if complete else None,
        "completeness_status": completeness,
        "llm_auto_estimation_connected": False,
        "manual_ai_estimation_connected": True,
        "account_quota_connected": draft.pricing_mode == PRICING_MODE_ACCOUNT_STRICT,
        "mode_notice": (
            "未匹配行将在后续阶段接入 LLM 自动估价；P2-2A 保持空值"
            if draft.pricing_mode == PRICING_MODE_ENTERPRISE_AI
            else "仅匹配当前账号已启用账户定额；未匹配行保持空值且不自动调用 LLM"
        ),
    }
    draft.row_count = row_count
    draft.matched_count = matched_count
    draft.priced_count = priced_count
    draft.pending_count = pending_count
    draft.manual_price_count = manual_price_count
    draft.quantity_unresolved_count = quantity_unresolved_count
    draft.priced_subtotal = subtotal
    draft.total_cost = subtotal if complete else None
    draft.completeness_status = completeness
    draft.summary_json = _json_dump(summary)
    db.flush()
    return summary


def _account_quota_snapshot(item: AccountQuotaItem) -> dict[str, Any]:
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
        "fingerprint": item.fingerprint,
        "notes": item.notes,
        "updated_at": _format_dt(item.updated_at),
    }


def _load_active_account_quota_catalog(
    db: Session,
    *,
    account_id: int,
) -> tuple[list[_QuotaEntry], dict[str, Any]]:
    rows = (
        db.query(AccountQuotaItem)
        .filter(
            AccountQuotaItem.account_id == account_id,
            AccountQuotaItem.status == ACCOUNT_QUOTA_STATUS_ACTIVE,
        )
        .order_by(AccountQuotaItem.id.asc())
        .with_for_update()
        .all()
    )
    entries: list[_QuotaEntry] = []
    for item in rows:
        item_name = str(item.item_name or "").strip()
        unit = str(item.unit or "").strip()
        normalized_unit = normalize_pricing_unit(unit)
        if not item_name or not unit or not normalized_unit:
            continue
        price = _q6(_decimal(item.unit_price))
        if price is None or price <= 0 or not _fits_numeric(price, _NUMERIC_20_6_MAX):
            continue
        feature_and_spec = " ".join(value for value in (item.item_features, item.spec) if value)
        snapshot = _account_quota_snapshot(item)
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
        "active_item_count": len(rows),
        "eligible_item_count": len(entries),
        "catalog_sha256": _sha256([entry.full_snapshot for entry in entries]),
    }


def _account_spec_exact(source_spec: str, entry: _QuotaEntry) -> bool:
    if not source_spec:
        return False
    if source_spec == entry.spec_norm:
        return True
    return len(source_spec) >= 4 and source_spec in f"{entry.name_norm}{entry.spec_norm}"


def _match_active_account_quota_source(
    source: dict[str, Any],
    catalog: list[_QuotaEntry],
) -> dict[str, Any]:
    """Strict account matching: exact code/name plus compatible unit only."""

    source_name = _normalize_text(source.get("item_name"))
    source_spec = _normalize_text(source.get("spec"))
    source_code = _normalize_text(source.get("quota_code"))
    source_unit = source.get("normalized_unit")
    records: list[dict[str, Any]] = []
    for entry in catalog:
        code_exact = bool(source_code and entry.code_norm and source_code == entry.code_norm)
        name_exact = bool(source_name and entry.name_norm and source_name == entry.name_norm)
        if not code_exact and not name_exact:
            continue
        compatible = bool(source_unit and source_unit == entry.normalized_unit)
        records.append(
            {
                "entry": entry,
                "code_exact": code_exact,
                "name_exact": name_exact,
                "spec_exact": _account_spec_exact(source_spec, entry),
                "unit_compatibility": "compatible" if compatible else "conflict",
            }
        )
    records.sort(key=lambda record: (record["entry"].quota_code or "\uffff", record["entry"].item_id))
    compatible_code = [record for record in records if record["code_exact"] and record["unit_compatibility"] == "compatible"]
    compatible_name = [record for record in records if record["name_exact"] and record["unit_compatibility"] == "compatible"]
    selected: dict[str, Any] | None = None
    rule = "no_active_account_quota_candidate"
    if len(compatible_code) == 1:
        selected, rule = compatible_code[0], "unique_active_account_code_and_unit"
    elif len(compatible_code) > 1:
        with_spec = [record for record in compatible_code if record["spec_exact"]]
        if len(with_spec) == 1:
            selected, rule = with_spec[0], "unique_active_account_code_spec_and_unit"
        else:
            rule = "ambiguous_active_account_code"
    elif len(compatible_name) == 1:
        selected, rule = compatible_name[0], "unique_active_account_name_and_unit"
    elif len(compatible_name) > 1:
        with_spec = [record for record in compatible_name if record["spec_exact"]]
        if len(with_spec) == 1:
            selected, rule = with_spec[0], "unique_active_account_name_spec_and_unit"
        else:
            rule = "ambiguous_active_account_name"

    if selected is not None:
        match_status = "auto_matched"
    elif records and all(record["unit_compatibility"] == "conflict" for record in records):
        match_status, rule = PRICING_MATCH_UNIT_CONFLICT, "active_account_quota_unit_conflict"
    elif records:
        match_status = "ambiguous"
    else:
        match_status = "unmatched"
    candidates = records[:5]
    for record in candidates:
        record["is_selected"] = record is selected
    return {
        "match_status": match_status,
        "selected": selected,
        "candidates": candidates,
        "reason": {
            "rule": rule,
            "source_name_normalized": source_name,
            "source_spec_normalized": source_spec,
            "source_unit_normalized": source_unit,
            "source_quota_code_normalized": source_code,
            "strict": True,
            "candidate_ids": [int(record["entry"].item_id) for record in candidates],
        },
    }


def create_or_rebuild_budget_pricing_draft(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    pricing_mode: str,
    source_import_batch_id: int,
    source_import_revision_id: int,
    expected_active_quota_version_id: int | None = None,
    expected_revision: int | None = None,
    reason: str | None = None,
) -> BudgetProjectPricingDraft:
    mode = _mode_guard(pricing_mode)
    account, _ = require_budget_project_account(
        db,
        project_id=profile.project_id,
        current_user=current_user,
        for_update=True,
    )
    locked_profile = _locked_profile(db, profile)
    batch, revision, formal_rows = _resolve_formal_source(
        db,
        locked_profile,
        expected_batch_id=source_import_batch_id,
        expected_revision_id=source_import_revision_id,
    )
    draft = (
        _current_draft_query(db, account_id=account.id, project_id=profile.project_id)
        .with_for_update()
        .one_or_none()
    )
    if draft is not None:
        exact_same_request = (
            draft.pricing_mode == mode
            and int(draft.source_import_batch_id) == int(batch.id)
            and int(draft.source_import_revision_id) == int(revision.id)
        )
        if expected_revision is None:
            if exact_same_request:
                return draft
            raise BudgetPricingError(
                "BUDGET_PRICING_DRAFT_EXPECTED_REVISION_REQUIRED",
                context={"current_revision": draft.revision},
            )
        if int(expected_revision) != int(draft.revision):
            raise BudgetPricingError(
                "BUDGET_PRICING_DRAFT_REVISION_CONFLICT",
                context={"expected_revision": expected_revision, "current_revision": draft.revision},
            )

    catalog = []
    catalog_stats: dict[str, Any] | None = None
    account_catalog_stats: dict[str, Any] | None = None
    quota_version = None
    catalog_index = None
    if mode == PRICING_MODE_ENTERPRISE_AI:
        quota_version = strict_active_quota_version(db, for_update=True)
        if (
            expected_active_quota_version_id is not None
            and int(expected_active_quota_version_id) != int(quota_version.id)
        ):
            raise BudgetPricingError(
                "BUDGET_PRICING_ACTIVE_QUOTA_CHANGED",
                context={"active_quota_version_id": quota_version.id},
            )
        catalog, catalog_stats = _load_quota_catalog(db, quota_version)
        if not catalog:
            raise BudgetPricingError("BUDGET_PRICING_QUOTA_CATALOG_EMPTY")
        catalog_index = _build_catalog_index(catalog)
    else:
        catalog, account_catalog_stats = _load_active_account_quota_catalog(
            db,
            account_id=account.id,
        )

    source_rows_sha256 = _sha256(formal_rows)
    source_snapshot = {
        "project_id": locked_profile.project_id,
        "batch_id": batch.id,
        "batch_uuid": batch.batch_uuid,
        "revision_id": revision.id,
        "revision_uuid": revision.revision_uuid,
        "revision_number": revision.revision_number,
        "revision_snapshot_sha256": revision.snapshot_sha256,
        "standard_rows": formal_rows,
    }
    previous_mode = draft.pricing_mode if draft else None
    previous_revision = int(draft.revision) if draft else None
    if draft is None:
        draft = BudgetProjectPricingDraft(
            draft_uuid=str(uuid4()),
            account_id=account.id,
            project_id=locked_profile.project_id,
            pricing_mode=mode,
            status=PRICING_DRAFT_STATUS_ACTIVE,
            revision=1,
            source_import_batch_id=batch.id,
            source_import_revision_id=revision.id,
            source_import_snapshot_sha256=revision.snapshot_sha256,
            source_rows_sha256=source_rows_sha256,
            source_snapshot_json=_json_dump(source_snapshot),
            enterprise_quota_version_id=quota_version.id if quota_version else None,
            enterprise_quota_catalog_sha256=catalog_stats["catalog_sha256"] if catalog_stats else None,
            account_quota_catalog_sha256=(
                account_catalog_stats["catalog_sha256"] if account_catalog_stats else None
            ),
            matching_engine_version=(MATCHING_ENGINE_VERSION if mode == PRICING_MODE_ENTERPRISE_AI else ACCOUNT_STRICT_ENGINE_VERSION),
            pricing_engine_version=PRICING_ENGINE_VERSION,
            summary_json="{}",
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(draft)
        db.flush()
    else:
        old_lines = (
            db.query(BudgetProjectPricingDraftLine)
            .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
            .with_for_update()
            .all()
        )
        for old_line in old_lines:
            db.delete(old_line)
        # Flush deletions before inserting replacement rows.  This keeps a
        # long-lived SQLAlchemy identity map from retaining stale draft-line
        # objects if a caller loaded lines before switching pricing modes.
        db.flush()
        draft.pricing_mode = mode
        draft.revision = int(draft.revision) + 1
        draft.source_import_batch_id = batch.id
        draft.source_import_revision_id = revision.id
        draft.source_import_snapshot_sha256 = revision.snapshot_sha256
        draft.source_rows_sha256 = source_rows_sha256
        draft.source_snapshot_json = _json_dump(source_snapshot)
        draft.enterprise_quota_version_id = quota_version.id if quota_version else None
        draft.enterprise_quota_catalog_sha256 = catalog_stats["catalog_sha256"] if catalog_stats else None
        draft.account_quota_catalog_sha256 = (
            account_catalog_stats["catalog_sha256"] if account_catalog_stats else None
        )
        draft.matching_engine_version = MATCHING_ENGINE_VERSION if mode == PRICING_MODE_ENTERPRISE_AI else ACCOUNT_STRICT_ENGINE_VERSION
        draft.pricing_engine_version = PRICING_ENGINE_VERSION
        draft.updated_by = current_user.id
        db.flush()

    for row in formal_rows:
        source = _source_values(row)
        selected_entry = None
        selected_account_entry = None
        selected_record: dict[str, Any] | None = None
        candidate_count = 0
        match_score = None
        if mode == PRICING_MODE_ENTERPRISE_AI:
            match = _match_source(source, catalog, catalog_index)
            pricing = _pricing_values(source, match)
            selected_record = match.get("selected")
            selected_entry = selected_record["entry"] if selected_record else None
            candidate_count = len(match["candidates"])
            match_score = (
                selected_record or (match["candidates"][0] if match["candidates"] else {})
            ).get("score")
            match_status = match["match_status"]
            base_status = pricing["pricing_status"]
            base_price = pricing["unit_price"]
            line_total = pricing["line_total"]
            amount_included = bool(pricing["amount_included"])
            price_source = "enterprise_quota" if base_price is not None else "none"
            evidence = {
                **match["reason"],
                "base_pricing_status": base_status,
                "base_price_source": "enterprise_quota" if base_price is not None else "none",
                "pricing_mode": mode,
            }
            warnings = list((row.get("standard_row") or {}).get("warnings") or [])
            warnings.extend(pricing.get("warning_codes") or [])
            if base_price is None:
                warnings.append("BUDGET_PRICING_DRAFT_LLM_NOT_CONNECTED")
        else:
            # Deliberately no enterprise quota query or fallback occurs in this
            # branch.  Only this account's active quota rows are eligible.
            match = _match_active_account_quota_source(source, catalog)
            pricing = _pricing_values(source, match)
            selected_record = match.get("selected")
            selected_account_entry = selected_record["entry"] if selected_record else None
            candidate_count = len(match["candidates"])
            match_score = None
            match_status = match["match_status"]
            base_status = pricing["pricing_status"]
            base_price = pricing["unit_price"]
            line_total = pricing["line_total"]
            amount_included = bool(pricing["amount_included"])
            price_source = "account_quota" if base_price is not None else "none"
            evidence = {
                **match["reason"],
                "base_pricing_status": base_status,
                "base_price_source": price_source,
                "pricing_mode": mode,
                "account_quota_catalog_sha256": account_catalog_stats["catalog_sha256"],
                "candidate_snapshots": [record["entry"].snapshot for record in match["candidates"]],
            }
            warnings = list((row.get("standard_row") or {}).get("warnings") or [])
            warnings.extend(pricing.get("warning_codes") or [])
            if base_price is None:
                warnings.append("BUDGET_PRICING_DRAFT_ACCOUNT_QUOTA_UNMATCHED")

        line = BudgetProjectPricingDraftLine(
            line_uuid=str(uuid4()),
            draft_id=draft.id,
            source_row_key=source["row_key"],
            source_sheet=source["source_sheet"],
            source_raw_row_index=source["raw_row_index"],
            source_sort_order=source["sort_order"],
            source_row_sha256=_sha256(row),
            source_row_snapshot_json=_json_dump(row),
            item_name=source["item_name"],
            spec=source["spec"],
            unit=source["unit"],
            calculation_quantity=source["quantity"],
            quantity_status=source["quantity_status"],
            match_status=match_status,
            pricing_status=base_status,
            candidate_count=candidate_count,
            match_score=match_score,
            match_evidence_json=_json_dump(evidence),
            selected_enterprise_quota_item_id=selected_entry.item_id if selected_entry else None,
            selected_account_quota_item_id=(
                selected_account_entry.item_id if selected_account_entry else None
            ),
            selected_source_snapshot_json=_json_dump(
                (selected_entry or selected_account_entry).full_snapshot
            ) if (selected_entry or selected_account_entry) else None,
            base_unit_price=base_price,
            ai_estimated_unit_price=None,
            ai_estimate_snapshot_json=None,
            manual_unit_price=None,
            effective_unit_price=base_price,
            line_total=line_total,
            amount_included=amount_included,
            price_source=price_source,
            warnings_json=_json_dump(list(dict.fromkeys(warnings))),
            line_revision=1,
            updated_by=current_user.id,
        )
        db.add(line)

    summary = _refresh_summary(db, draft)
    event_type = (
        "draft_created"
        if previous_revision is None
        else ("mode_switched" if previous_mode != mode else "draft_rebuilt")
    )
    _append_event(
        db,
        draft=draft,
        current_user=current_user,
        event_type=event_type,
        from_mode=previous_mode,
        from_revision=previous_revision,
        event={
            "reason": (reason or "").strip()[:2000] or None,
            "source_import_batch_id": batch.id,
            "source_import_revision_id": revision.id,
            "enterprise_quota_version_id": quota_version.id if quota_version else None,
            "account_quota_catalog_sha256": (
                account_catalog_stats["catalog_sha256"] if account_catalog_stats else None
            ),
            "summary": summary,
        },
    )
    db.flush()
    return draft


def get_budget_pricing_draft_line(
    db: Session,
    draft: BudgetProjectPricingDraft,
    identifier: str | int,
    *,
    for_update: bool = False,
) -> BudgetProjectPricingDraftLine:
    text = str(identifier).strip()
    query = db.query(BudgetProjectPricingDraftLine).filter(
        BudgetProjectPricingDraftLine.draft_id == draft.id
    )
    query = (
        query.filter(BudgetProjectPricingDraftLine.id == int(text))
        if text.isdigit()
        else query.filter(BudgetProjectPricingDraftLine.line_uuid == text)
    )
    if for_update:
        query = query.with_for_update()
    line = query.one_or_none()
    if line is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_LINE_NOT_FOUND", status_code=404)
    return line


def patch_budget_pricing_draft_line(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    line_identifier: str | int,
    expected_revision: int,
    expected_line_revision: int,
    manual_unit_price: Decimal | None,
    reason: str | None = None,
) -> tuple[BudgetProjectPricingDraft, BudgetProjectPricingDraftLine]:
    draft = get_current_budget_pricing_draft(
        db,
        profile,
        current_user,
        for_update=True,
    )
    if draft is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
    if int(draft.revision) != int(expected_revision):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_REVISION_CONFLICT",
            context={"expected_revision": expected_revision, "current_revision": draft.revision},
        )
    line = get_budget_pricing_draft_line(db, draft, line_identifier, for_update=True)
    if int(line.line_revision) != int(expected_line_revision):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_LINE_REVISION_CONFLICT",
            context={
                "expected_line_revision": expected_line_revision,
                "current_line_revision": line.line_revision,
            },
        )
    parsed = _decimal(manual_unit_price)
    if manual_unit_price is not None and (
        parsed is None or parsed <= 0 or not _fits_numeric(parsed, _NUMERIC_20_6_MAX)
    ):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_MANUAL_PRICE_INVALID",
            status_code=422,
        )
    previous_price = _decimal_text(line.manual_unit_price)
    previous_revision = int(draft.revision)
    _apply_effective_price(line, manual_unit_price=parsed)
    line.line_revision = int(line.line_revision) + 1
    line.updated_by = current_user.id
    draft.revision = int(draft.revision) + 1
    draft.updated_by = current_user.id
    summary = _refresh_summary(db, draft)
    _append_event(
        db,
        draft=draft,
        current_user=current_user,
        event_type=("manual_price_cleared" if parsed is None else "manual_price_updated"),
        from_mode=draft.pricing_mode,
        from_revision=previous_revision,
        event={
            "line_id": line.id,
            "line_uuid": line.line_uuid,
            "previous_manual_unit_price": previous_price,
            "manual_unit_price": _decimal_text(parsed),
            "reason": (reason or "").strip()[:2000] or None,
            "summary": summary,
        },
    )
    db.flush()
    return draft, line


def serialize_budget_pricing_draft(draft: BudgetProjectPricingDraft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "draft_uuid": draft.draft_uuid,
        "account_id": draft.account_id,
        "project_id": draft.project_id,
        "pricing_mode": draft.pricing_mode,
        "status": draft.status,
        "revision": draft.revision,
        "source_import_batch_id": draft.source_import_batch_id,
        "source_import_revision_id": draft.source_import_revision_id,
        "source_import_snapshot_sha256": draft.source_import_snapshot_sha256,
        "source_rows_sha256": draft.source_rows_sha256,
        "enterprise_quota_version_id": draft.enterprise_quota_version_id,
        "enterprise_quota_catalog_sha256": draft.enterprise_quota_catalog_sha256,
        "account_quota_catalog_sha256": draft.account_quota_catalog_sha256,
        "matching_engine_version": draft.matching_engine_version,
        "pricing_engine_version": draft.pricing_engine_version,
        "row_count": draft.row_count,
        "matched_count": draft.matched_count,
        "priced_count": draft.priced_count,
        "pending_count": draft.pending_count,
        "manual_price_count": draft.manual_price_count,
        "quantity_unresolved_count": draft.quantity_unresolved_count,
        "priced_subtotal": _decimal_text(draft.priced_subtotal),
        "total_cost": _decimal_text(draft.total_cost),
        "completeness_status": draft.completeness_status,
        "summary": _json_load(draft.summary_json, {}),
        "created_by": draft.created_by,
        "updated_by": draft.updated_by,
        "created_at": _format_dt(draft.created_at),
        "updated_at": _format_dt(draft.updated_at),
    }


def serialize_budget_pricing_draft_line(line: BudgetProjectPricingDraftLine) -> dict[str, Any]:
    return {
        "id": line.id,
        "line_uuid": line.line_uuid,
        "draft_id": line.draft_id,
        "source_row_key": line.source_row_key,
        "source_sheet": line.source_sheet,
        "source_raw_row_index": line.source_raw_row_index,
        "source_sort_order": line.source_sort_order,
        "item_name": line.item_name,
        "spec": line.spec,
        "unit": line.unit,
        "quantity": _decimal_text(line.calculation_quantity),
        "calculation_quantity": _decimal_text(line.calculation_quantity),
        "quantity_status": line.quantity_status,
        "match_status": line.match_status,
        "pricing_status": line.pricing_status,
        "candidate_count": line.candidate_count,
        "match_score": _decimal_text(line.match_score),
        "match_evidence": _json_load(line.match_evidence_json, {}),
        "selected_enterprise_quota_item_id": line.selected_enterprise_quota_item_id,
        "selected_account_quota_item_id": line.selected_account_quota_item_id,
        "selected_source": _json_load(line.selected_source_snapshot_json, None),
        "base_unit_price": _decimal_text(line.base_unit_price),
        "ai_estimated_unit_price": _decimal_text(line.ai_estimated_unit_price),
        "ai_estimate": _json_load(line.ai_estimate_snapshot_json, None),
        "manual_unit_price": _decimal_text(line.manual_unit_price),
        "effective_unit_price": _decimal_text(line.effective_unit_price),
        "line_total": _decimal_text(line.line_total),
        "amount_included": bool(line.amount_included),
        "price_source": line.price_source,
        "warnings": _json_load(line.warnings_json, []),
        "line_revision": line.line_revision,
        "updated_by": line.updated_by,
        "created_at": _format_dt(line.created_at),
        "updated_at": _format_dt(line.updated_at),
    }
