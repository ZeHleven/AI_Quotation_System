"""Confirmed synchronization from mutable pricing drafts to account quotas.

This is intentionally a one-way, account-scoped learning action.  It never
rebuilds the source draft, never creates a formal pricing run, and never reads
or writes the enterprise quota catalog.  Preview is read-only; confirmation is
transactional and records immutable source/target evidence.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.account_quota import (
    ACCOUNT_QUOTA_SOURCE_PRICING_DRAFT_SYNC,
    ACCOUNT_QUOTA_STATUS_ARCHIVED,
    ACCOUNT_QUOTA_STATUS_DRAFT,
    ACCOUNT_QUOTA_SYNC_ACTION_CREATE,
    ACCOUNT_QUOTA_SYNC_ACTION_SKIP,
    ACCOUNT_QUOTA_SYNC_ACTION_UPDATE_EXISTING,
    ACCOUNT_QUOTA_SYNC_STATUS_COMPLETED,
    AccountQuotaItem,
    AccountQuotaSyncLine,
    AccountQuotaSyncRun,
)
from app.models.budget_pricing_draft import BudgetProjectPricingDraft, BudgetProjectPricingDraftLine
from app.models.budget_project import BudgetProjectProfile
from app.models.user import User
from app.schemas.budget_pricing import (
    BudgetPricingDraftAccountQuotaSyncConfirmIn,
    BudgetPricingDraftAccountQuotaSyncPreviewIn,
)
from app.services.account_quotas import (
    AccountQuotaError,
    build_account_quota_fingerprint,
    create_account_quota_item_from_pricing_draft_sync,
    snapshot_account_quota_item,
    update_account_quota_item_from_pricing_draft_sync,
)
from app.services.account_tenancy import resolve_current_account
from app.services.budget_pricing import BudgetPricingError
from app.services.budget_pricing_drafts import get_current_budget_pricing_draft


_Q6 = Decimal("0.000001")
_MAX_UNIT_PRICE = Decimal("999999999999.999999")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decimal_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value)).quantize(_Q6)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return format(parsed, "f")


def _positive_price(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0 or parsed > _MAX_UNIT_PRICE:
        return None
    try:
        return parsed.quantize(_Q6)
    except InvalidOperation:
        return None


def _identifier_for_line(line: BudgetProjectPricingDraftLine) -> str:
    return line.line_uuid or str(line.id)


def _sync_price_from_line(line: BudgetProjectPricingDraftLine) -> tuple[Decimal | None, str]:
    manual = _positive_price(line.manual_unit_price)
    if manual is not None:
        return manual, "manual"

    effective = _positive_price(line.effective_unit_price)
    if effective is not None:
        source = str(line.price_source or "").strip() or "effective"
        return effective, source

    ai_estimate = _positive_price(line.ai_estimated_unit_price)
    if ai_estimate is not None:
        return ai_estimate, "ai_estimate"

    base = _positive_price(line.base_unit_price)
    if base is not None:
        source = str(line.price_source or "").strip() or "base"
        return base, source

    return None, "none"


def _source_snapshot(line: BudgetProjectPricingDraftLine) -> dict[str, Any]:
    sync_price, sync_price_source = _sync_price_from_line(line)
    return {
        "draft_line_id": int(line.id),
        "line_uuid": line.line_uuid,
        "line_revision": int(line.line_revision),
        "source_row_key": line.source_row_key,
        "source_sheet": line.source_sheet,
        "source_raw_row_index": int(line.source_raw_row_index),
        "item_name": line.item_name,
        "item_features": None,
        "spec": line.spec,
        "unit": line.unit,
        "quantity": _decimal_text(line.calculation_quantity),
        "base_unit_price": _decimal_text(line.base_unit_price),
        "ai_estimated_unit_price": _decimal_text(line.ai_estimated_unit_price),
        "manual_unit_price": _decimal_text(line.manual_unit_price),
        "effective_unit_price": _decimal_text(line.effective_unit_price),
        "sync_unit_price": _decimal_text(sync_price),
        "sync_price_source": sync_price_source,
        "price_source": line.price_source,
        "match_status": line.match_status,
        "pricing_status": line.pricing_status,
    }


def _load_lines(
    db: Session,
    *,
    draft: BudgetProjectPricingDraft,
    identifiers: Iterable[str] | None,
    for_update: bool,
) -> list[BudgetProjectPricingDraftLine]:
    query = db.query(BudgetProjectPricingDraftLine).filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
    requested = [str(value).strip() for value in identifiers or [] if str(value).strip()]
    if not requested:
        query = query.filter(
            or_(
                BudgetProjectPricingDraftLine.manual_unit_price.isnot(None),
                BudgetProjectPricingDraftLine.effective_unit_price.isnot(None),
                BudgetProjectPricingDraftLine.ai_estimated_unit_price.isnot(None),
                BudgetProjectPricingDraftLine.base_unit_price.isnot(None),
            )
        )
        if for_update:
            query = query.with_for_update()
        return query.order_by(BudgetProjectPricingDraftLine.source_sort_order, BudgetProjectPricingDraftLine.id).all()

    numeric_ids = [int(value) for value in requested if value.isdigit()]
    uuids = [value for value in requested if not value.isdigit()]
    predicates = []
    if numeric_ids:
        predicates.append(BudgetProjectPricingDraftLine.id.in_(numeric_ids))
    if uuids:
        predicates.append(BudgetProjectPricingDraftLine.line_uuid.in_(uuids))
    if not predicates:
        raise BudgetPricingError("ACCOUNT_QUOTA_SYNC_LINE_NOT_FOUND", status_code=404)
    query = query.filter(or_(*predicates))
    if for_update:
        query = query.with_for_update()
    rows = query.all()
    by_identifier = {str(line.id): line for line in rows}
    by_identifier.update({line.line_uuid: line for line in rows if line.line_uuid})
    missing = [value for value in requested if value not in by_identifier]
    if missing:
        raise BudgetPricingError(
            "ACCOUNT_QUOTA_SYNC_LINE_NOT_FOUND",
            status_code=404,
            context={"line_identifier": missing[0]},
        )
    return [by_identifier[value] for value in requested]


def _ensure_draft_revision(draft: BudgetProjectPricingDraft, expected_revision: int) -> None:
    if int(draft.revision) != int(expected_revision):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_REVISION_CONFLICT",
            context={"expected_revision": int(expected_revision), "current_revision": int(draft.revision)},
        )


def _existing_items_by_fingerprint(
    db: Session,
    *,
    account_id: int,
    fingerprints: Iterable[str],
    for_update: bool,
) -> dict[str, AccountQuotaItem]:
    values = sorted({value for value in fingerprints if value})
    if not values:
        return {}
    query = db.query(AccountQuotaItem).filter(
        AccountQuotaItem.account_id == account_id,
        AccountQuotaItem.fingerprint.in_(values),
    )
    if for_update:
        query = query.with_for_update()
    return {item.fingerprint: item for item in query.all()}


def _candidate_from_line(line: BudgetProjectPricingDraftLine, existing: AccountQuotaItem | None) -> dict[str, Any]:
    snapshot = _source_snapshot(line)
    price = _positive_price(snapshot.get("sync_unit_price"))
    item_name = str(line.item_name or "").strip()
    unit = str(line.unit or "").strip()
    candidate: dict[str, Any] = {
        "line_identifier": _identifier_for_line(line),
        "draft_line_id": int(line.id),
        "expected_line_revision": int(line.line_revision),
        "source": snapshot,
        "fingerprint": None,
        "eligible": False,
        "block_code": None,
        "existing_item": None,
        "suggested_action": ACCOUNT_QUOTA_SYNC_ACTION_SKIP,
        "allowed_actions": [ACCOUNT_QUOTA_SYNC_ACTION_SKIP],
        "target_status": ACCOUNT_QUOTA_STATUS_DRAFT,
        "sync_unit_price": price,
        "sync_price_source": snapshot.get("sync_price_source") or "none",
    }
    if price is None:
        candidate["block_code"] = "ACCOUNT_QUOTA_SYNC_PRICE_REQUIRED"
        return candidate
    if not item_name:
        candidate["block_code"] = "ACCOUNT_QUOTA_SYNC_ITEM_NAME_REQUIRED"
        return candidate
    if not unit:
        candidate["block_code"] = "ACCOUNT_QUOTA_SYNC_UNIT_REQUIRED"
        return candidate
    try:
        fingerprint = build_account_quota_fingerprint(
            item_name=item_name,
            item_features=None,
            spec=line.spec,
            unit=unit,
        )
    except AccountQuotaError as exc:
        candidate["block_code"] = exc.code
        return candidate
    candidate["fingerprint"] = fingerprint
    candidate["eligible"] = True
    if existing is None:
        candidate["suggested_action"] = ACCOUNT_QUOTA_SYNC_ACTION_CREATE
        candidate["allowed_actions"] = [ACCOUNT_QUOTA_SYNC_ACTION_CREATE, ACCOUNT_QUOTA_SYNC_ACTION_SKIP]
        return candidate
    existing_snapshot = snapshot_account_quota_item(existing)
    candidate["existing_item"] = existing_snapshot
    if existing.status == ACCOUNT_QUOTA_STATUS_ARCHIVED:
        candidate["eligible"] = False
        candidate["block_code"] = "ACCOUNT_QUOTA_SYNC_ARCHIVED_TARGET"
        return candidate
    candidate["allowed_actions"] = [ACCOUNT_QUOTA_SYNC_ACTION_UPDATE_EXISTING, ACCOUNT_QUOTA_SYNC_ACTION_SKIP]
    return candidate


def _build_candidates(
    db: Session,
    *,
    account_id: int,
    lines: list[BudgetProjectPricingDraftLine],
    for_update: bool,
) -> list[dict[str, Any]]:
    provisional = [_candidate_from_line(line, None) for line in lines]
    existing = _existing_items_by_fingerprint(
        db,
        account_id=account_id,
        fingerprints=[candidate["fingerprint"] for candidate in provisional if candidate.get("fingerprint")],
        for_update=for_update,
    )
    candidates = [_candidate_from_line(line, existing.get(candidate["fingerprint"]) if candidate.get("fingerprint") else None)
                  for line, candidate in zip(lines, provisional)]
    by_fingerprint: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if candidate.get("eligible") and candidate.get("fingerprint"):
            by_fingerprint.setdefault(candidate["fingerprint"], []).append(candidate)
    for grouped in by_fingerprint.values():
        if len(grouped) <= 1:
            continue
        for duplicate in grouped[1:]:
            duplicate["eligible"] = False
            duplicate["block_code"] = "ACCOUNT_QUOTA_SYNC_DUPLICATE_SELECTION"
            duplicate["suggested_action"] = ACCOUNT_QUOTA_SYNC_ACTION_SKIP
            duplicate["allowed_actions"] = [ACCOUNT_QUOTA_SYNC_ACTION_SKIP]
    return candidates


def _serialize_preview_item(candidate: dict[str, Any]) -> dict[str, Any]:
    source = candidate["source"]
    return {
        "line_identifier": candidate["line_identifier"],
        "draft_line_id": candidate["draft_line_id"],
        "expected_line_revision": candidate["expected_line_revision"],
        "item_name": source["item_name"],
        "spec": source["spec"],
        "unit": source["unit"],
        "manual_unit_price": source["manual_unit_price"],
        "effective_unit_price": source["effective_unit_price"],
        "sync_unit_price": source["sync_unit_price"],
        "sync_price_source": source["sync_price_source"],
        "price_source": source["price_source"],
        "fingerprint": candidate["fingerprint"],
        "eligible": candidate["eligible"],
        "block_code": candidate["block_code"],
        "existing_item": candidate["existing_item"],
        "suggested_action": candidate["suggested_action"],
        "allowed_actions": candidate["allowed_actions"],
        "target_status": candidate["target_status"],
        "source": source,
    }


def preview_account_quota_sync(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    payload: BudgetPricingDraftAccountQuotaSyncPreviewIn,
) -> dict[str, Any]:
    account = resolve_current_account(db, current_user)
    draft = get_current_budget_pricing_draft(db, profile, current_user)
    if draft is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
    _ensure_draft_revision(draft, payload.expected_revision)
    lines = _load_lines(db, draft=draft, identifiers=payload.line_identifiers, for_update=False)
    candidates = _build_candidates(db, account_id=account.id, lines=lines, for_update=False)
    rows = [_serialize_preview_item(candidate) for candidate in candidates]
    return {
        "draft_id": int(draft.id),
        "draft_uuid": draft.draft_uuid,
        "draft_revision": int(draft.revision),
        "items": rows,
        "summary": {
            "requested_count": len(rows),
            "eligible_create_count": sum(1 for row in rows if row["suggested_action"] == ACCOUNT_QUOTA_SYNC_ACTION_CREATE),
            "existing_decision_count": sum(1 for row in rows if row["existing_item"] is not None and row["eligible"]),
            "blocked_count": sum(1 for row in rows if not row["eligible"]),
        },
        "boundary": {
            "only_manual_prices": False,
            "price_scope": "priced_draft_lines",
            "target_status": ACCOUNT_QUOTA_STATUS_DRAFT,
            "does_not_reprice_current_draft": True,
            "does_not_change_enterprise_quota": True,
            "does_not_create_formal_run": True,
        },
    }


def _sync_reason(reason: str, line: BudgetProjectPricingDraftLine) -> str:
    return (
        f"{reason.strip()}；来源：计价草稿行 {line.source_sheet} / {line.source_raw_row_index}"
    )[:2000]


def _add_sync_line(
    db: Session,
    *,
    sync_run: AccountQuotaSyncRun,
    line: BudgetProjectPricingDraftLine,
    candidate: dict[str, Any],
    action: str,
    outcome: str,
    target: AccountQuotaItem | None = None,
    target_before: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    target_after = snapshot_account_quota_item(target) if target is not None else None
    db.add(
        AccountQuotaSyncLine(
            sync_run_id=sync_run.id,
            account_id=sync_run.account_id,
            draft_id=sync_run.draft_id,
            draft_line_id=line.id,
            source_line_uuid=line.line_uuid,
            source_line_revision=line.line_revision,
            fingerprint=candidate.get("fingerprint"),
            action=action,
            outcome=outcome,
            account_quota_item_id=target.id if target is not None else None,
            target_item_revision=target.revision if target is not None else None,
            source_snapshot_json=_json_dump(candidate["source"]),
            target_before_snapshot_json=_json_dump(target_before) if target_before is not None else None,
            target_after_snapshot_json=_json_dump(target_after) if target_after is not None else None,
            result_json=_json_dump(result or {}),
        )
    )


def confirm_account_quota_sync(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    payload: BudgetPricingDraftAccountQuotaSyncConfirmIn,
) -> dict[str, Any]:
    account = resolve_current_account(db, current_user, for_update=True)
    draft = get_current_budget_pricing_draft(db, profile, current_user, for_update=True)
    if draft is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
    _ensure_draft_revision(draft, payload.expected_revision)
    requested = {item.line_identifier.strip(): item for item in payload.items}
    lines = _load_lines(db, draft=draft, identifiers=requested.keys(), for_update=True)
    if len({line.id for line in lines}) != len(lines):
        raise BudgetPricingError("ACCOUNT_QUOTA_SYNC_DUPLICATE_SELECTION", status_code=422)
    request_by_line_id = {
        line.id: requested.get(line.line_uuid) or requested.get(str(line.id))
        for line in lines
    }
    if any(request is None for request in request_by_line_id.values()):
        raise BudgetPricingError("ACCOUNT_QUOTA_SYNC_LINE_NOT_FOUND", status_code=404)
    candidates = _build_candidates(db, account_id=account.id, lines=lines, for_update=True)
    by_identifier = {candidate["line_identifier"]: candidate for candidate in candidates}
    active_fingerprints = [
        by_identifier[_identifier_for_line(line)].get("fingerprint")
        for line in lines
        if request_by_line_id[line.id].action != ACCOUNT_QUOTA_SYNC_ACTION_SKIP
        and by_identifier[_identifier_for_line(line)].get("fingerprint")
    ]
    if len(active_fingerprints) != len(set(active_fingerprints)):
        raise BudgetPricingError("ACCOUNT_QUOTA_SYNC_DUPLICATE_SELECTION", status_code=422)

    sync_run = AccountQuotaSyncRun(
        sync_uuid=str(uuid4()),
        account_id=account.id,
        project_id=profile.project_id,
        draft_id=draft.id,
        draft_revision=draft.revision,
        status=ACCOUNT_QUOTA_SYNC_STATUS_COMPLETED,
        requested_count=len(lines),
        reason=payload.reason.strip(),
        actor_id=current_user.id,
    )
    db.add(sync_run)
    db.flush()

    created_count = updated_count = skipped_count = 0
    existing = _existing_items_by_fingerprint(
        db,
        account_id=account.id,
        fingerprints=active_fingerprints,
        for_update=True,
    )
    for line in lines:
        identifier = _identifier_for_line(line)
        request = request_by_line_id[line.id]
        candidate = by_identifier[identifier]
        action = request.action
        if int(line.line_revision) != int(request.expected_line_revision):
            raise BudgetPricingError(
                "BUDGET_PRICING_DRAFT_LINE_REVISION_CONFLICT",
                context={
                    "line_identifier": identifier,
                    "expected_line_revision": request.expected_line_revision,
                    "current_line_revision": line.line_revision,
                },
            )
        if action == ACCOUNT_QUOTA_SYNC_ACTION_SKIP:
            skipped_count += 1
            _add_sync_line(
                db,
                sync_run=sync_run,
                line=line,
                candidate=candidate,
                action=action,
                outcome="skipped",
                result={"block_code": candidate.get("block_code")},
            )
            continue
        if not candidate["eligible"]:
            raise BudgetPricingError(
                "ACCOUNT_QUOTA_SYNC_LINE_NOT_ELIGIBLE",
                status_code=422,
                context={"line_identifier": identifier, "block_code": candidate.get("block_code")},
            )
        target = existing.get(candidate["fingerprint"])
        source_price = candidate["sync_unit_price"]
        reason = _sync_reason(payload.reason, line)
        if action == ACCOUNT_QUOTA_SYNC_ACTION_CREATE:
            if target is not None:
                raise BudgetPricingError(
                    "ACCOUNT_QUOTA_SYNC_EXISTING_ITEM_DECISION_REQUIRED",
                    context={"line_identifier": identifier, "existing_item_id": target.id},
                )
            try:
                target = create_account_quota_item_from_pricing_draft_sync(
                    db,
                    current_user,
                    item_name=line.item_name or "",
                    item_features=None,
                    spec=line.spec,
                    unit=line.unit or "",
                    unit_price=source_price,
                    reason=reason,
                )
            except AccountQuotaError as exc:
                raise BudgetPricingError(exc.code, status_code=exc.status_code, context=exc.context) from exc
            existing[target.fingerprint] = target
            created_count += 1
            _add_sync_line(
                db,
                sync_run=sync_run,
                line=line,
                candidate=candidate,
                action=action,
                outcome="created",
                target=target,
                result={"source": ACCOUNT_QUOTA_SOURCE_PRICING_DRAFT_SYNC, "status": target.status},
            )
            continue
        if action != ACCOUNT_QUOTA_SYNC_ACTION_UPDATE_EXISTING:
            raise BudgetPricingError("ACCOUNT_QUOTA_SYNC_ACTION_INVALID", status_code=422)
        if target is None:
            raise BudgetPricingError("ACCOUNT_QUOTA_SYNC_TARGET_NOT_FOUND", context={"line_identifier": identifier})
        if target.status == ACCOUNT_QUOTA_STATUS_ARCHIVED:
            raise BudgetPricingError("ACCOUNT_QUOTA_SYNC_ARCHIVED_TARGET", context={"line_identifier": identifier})
        if request.expected_target_revision is None or int(target.revision) != int(request.expected_target_revision):
            raise BudgetPricingError(
                "ACCOUNT_QUOTA_SYNC_TARGET_REVISION_CONFLICT",
                context={
                    "line_identifier": identifier,
                    "expected_target_revision": request.expected_target_revision,
                    "current_target_revision": target.revision,
                },
            )
        try:
            target, before = update_account_quota_item_from_pricing_draft_sync(
                db,
                current_user,
                target,
                expected_revision=request.expected_target_revision,
                unit_price=source_price,
                reason=reason,
            )
        except AccountQuotaError as exc:
            raise BudgetPricingError(exc.code, status_code=exc.status_code, context=exc.context) from exc
        updated_count += 1
        _add_sync_line(
            db,
            sync_run=sync_run,
            line=line,
            candidate=candidate,
            action=action,
            outcome="updated_existing",
            target=target,
            target_before=before,
            result={"status": target.status, "previous_status": before.get("status")},
        )

    sync_run.created_count = created_count
    sync_run.updated_count = updated_count
    sync_run.skipped_count = skipped_count
    db.flush()
    return {
        "sync_run_id": int(sync_run.id),
        "sync_uuid": sync_run.sync_uuid,
        "draft_id": int(draft.id),
        "draft_revision": int(draft.revision),
        "created_count": created_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "target_status": ACCOUNT_QUOTA_STATUS_DRAFT,
        "does_not_reprice_current_draft": True,
    }
