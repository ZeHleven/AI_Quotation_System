"""Account-scoped quota catalog CRUD, lifecycle and audit history."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import unicodedata
import uuid
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account_quota import (
    ACCOUNT_QUOTA_EVENT_CREATED,
    ACCOUNT_QUOTA_EVENT_PRICING_DRAFT_SYNCED,
    ACCOUNT_QUOTA_EVENT_STATUS_CHANGED,
    ACCOUNT_QUOTA_EVENT_UPDATED,
    ACCOUNT_QUOTA_SOURCE_MANUAL,
    ACCOUNT_QUOTA_SOURCE_PRICING_DRAFT_SYNC,
    ACCOUNT_QUOTA_SOURCE_VALUES,
    ACCOUNT_QUOTA_STATUS_ACTIVE,
    ACCOUNT_QUOTA_STATUS_ARCHIVED,
    ACCOUNT_QUOTA_STATUS_DRAFT,
    ACCOUNT_QUOTA_STATUS_VALUES,
    AccountQuotaItem,
    AccountQuotaItemHistory,
)
from app.models.user import User
from app.schemas.account_quota import AccountQuotaCreateIn, AccountQuotaStatusIn, AccountQuotaUpdateIn
from app.services.account_tenancy import resolve_current_account


_Q6 = Decimal("0.000001")
_MAX_UNIT_PRICE = Decimal("999999999999.999999")
_MUTABLE_FIELDS = {
    "quota_code",
    "item_name",
    "item_features",
    "spec",
    "unit",
    "unit_price",
    "notes",
}


class AccountQuotaError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409, context: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.context = context or {}

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _clean_required(value: Any, field_name: str) -> str:
    cleaned = _clean_optional(value)
    if cleaned is None:
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_INVALID_FIELD",
            status_code=422,
            context={"field": field_name},
        )
    return cleaned


def _normalize_component(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())


def build_account_quota_fingerprint(
    *,
    item_name: str,
    item_features: str | None,
    spec: str | None,
    unit: str,
) -> str:
    """Stable identity key; quota code and price deliberately do not define identity."""

    normalized = {
        "item_name": _normalize_component(item_name),
        "item_features": _normalize_component(item_features),
        "spec": _normalize_component(spec),
        "unit": _normalize_component(unit),
    }
    if not normalized["item_name"]:
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_INVALID_FIELD",
            status_code=422,
            context={"field": "item_name"},
        )
    if not normalized["unit"]:
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_INVALID_FIELD",
            status_code=422,
            context={"field": "unit"},
        )
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unit_price(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_INVALID_FIELD",
            status_code=422,
            context={"field": "unit_price"},
        ) from exc
    if not parsed.is_finite() or parsed <= 0 or parsed > _MAX_UNIT_PRICE:
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_INVALID_FIELD",
            status_code=422,
            context={"field": "unit_price"},
        )
    try:
        return parsed.quantize(_Q6, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_INVALID_FIELD",
            status_code=422,
            context={"field": "unit_price"},
        ) from exc


def _decimal_text(value: Any) -> str:
    return format(_unit_price(value), "f")


def _datetime_text(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _snapshot(item: AccountQuotaItem) -> dict[str, Any]:
    return {
        "id": int(item.id),
        "item_uuid": item.item_uuid,
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
        "created_by": int(item.created_by),
        "updated_by": int(item.updated_by),
    }


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _duplicate_query(db: Session, *, account_id: int, fingerprint: str, exclude_id: int | None = None):
    query = db.query(AccountQuotaItem).filter(
        AccountQuotaItem.account_id == account_id,
        AccountQuotaItem.fingerprint == fingerprint,
    )
    if exclude_id is not None:
        query = query.filter(AccountQuotaItem.id != exclude_id)
    return query


def _raise_duplicate(item: AccountQuotaItem | None, fingerprint: str) -> None:
    context: dict[str, Any] = {"fingerprint": fingerprint}
    if item is not None:
        context.update(
            {
                "existing_item_id": int(item.id),
                "existing_item_uuid": item.item_uuid,
                "existing_status": item.status,
            }
        )
    raise AccountQuotaError("ACCOUNT_QUOTA_DUPLICATE_FINGERPRINT", context=context)


def _append_history(
    db: Session,
    *,
    item: AccountQuotaItem,
    actor: User,
    event_type: str,
    before: dict[str, Any] | None,
    reason: str | None,
) -> AccountQuotaItemHistory:
    history = AccountQuotaItemHistory(
        account_quota_item_id=item.id,
        account_id=item.account_id,
        revision=item.revision,
        event_type=event_type,
        from_status=before.get("status") if before else None,
        to_status=item.status,
        before_snapshot_json=_json_dump(before) if before is not None else None,
        after_snapshot_json=_json_dump(_snapshot(item)),
        reason=_clean_optional(reason),
        actor_id=actor.id,
    )
    db.add(history)
    return history


def _scoped_item_query(
    db: Session,
    *,
    account_id: int,
    identifier: str | int,
):
    query = db.query(AccountQuotaItem).filter(AccountQuotaItem.account_id == account_id)
    text = str(identifier).strip()
    if text.isdigit():
        return query.filter(AccountQuotaItem.id == int(text))
    return query.filter(AccountQuotaItem.item_uuid == text)


def get_account_quota_item(
    db: Session,
    current_user: User,
    identifier: str | int,
    *,
    for_update: bool = False,
) -> AccountQuotaItem:
    account = resolve_current_account(db, current_user, for_update=for_update)
    query = _scoped_item_query(db, account_id=account.id, identifier=identifier)
    if for_update:
        query = query.with_for_update()
    item = query.one_or_none()
    if item is None:
        raise AccountQuotaError("ACCOUNT_QUOTA_NOT_FOUND", status_code=404)
    return item


def list_account_quota_items(
    db: Session,
    current_user: User,
    *,
    status_filter: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AccountQuotaItem], int]:
    account = resolve_current_account(db, current_user)
    query = db.query(AccountQuotaItem).filter(AccountQuotaItem.account_id == account.id)
    if status_filter:
        if status_filter not in ACCOUNT_QUOTA_STATUS_VALUES:
            raise AccountQuotaError(
                "ACCOUNT_QUOTA_STATUS_INVALID",
                status_code=422,
                context={"status": status_filter},
            )
        query = query.filter(AccountQuotaItem.status == status_filter)
    if source:
        if source not in ACCOUNT_QUOTA_SOURCE_VALUES:
            raise AccountQuotaError(
                "ACCOUNT_QUOTA_SOURCE_INVALID",
                status_code=422,
                context={"source": source},
            )
        query = query.filter(AccountQuotaItem.source == source)
    cleaned_keyword = _clean_optional(keyword)
    if cleaned_keyword:
        pattern = f"%{cleaned_keyword}%"
        query = query.filter(
            or_(
                AccountQuotaItem.quota_code.like(pattern),
                AccountQuotaItem.item_name.like(pattern),
                AccountQuotaItem.item_features.like(pattern),
                AccountQuotaItem.spec.like(pattern),
                AccountQuotaItem.unit.like(pattern),
            )
        )
    total = query.count()
    rows = (
        query.order_by(AccountQuotaItem.updated_at.desc(), AccountQuotaItem.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def create_account_quota_item(
    db: Session,
    current_user: User,
    payload: AccountQuotaCreateIn,
) -> AccountQuotaItem:
    account = resolve_current_account(db, current_user, for_update=True)
    item_name = _clean_required(payload.item_name, "item_name")
    unit = _clean_required(payload.unit, "unit")
    item_features = _clean_optional(payload.item_features)
    spec = _clean_optional(payload.spec)
    fingerprint = build_account_quota_fingerprint(
        item_name=item_name,
        item_features=item_features,
        spec=spec,
        unit=unit,
    )
    duplicate = _duplicate_query(db, account_id=account.id, fingerprint=fingerprint).with_for_update().one_or_none()
    if duplicate is not None:
        _raise_duplicate(duplicate, fingerprint)
    item = AccountQuotaItem(
        item_uuid=str(uuid.uuid4()),
        account_id=account.id,
        quota_code=_clean_optional(payload.quota_code),
        item_name=item_name,
        item_features=item_features,
        spec=spec,
        unit=unit,
        unit_price=_unit_price(payload.unit_price),
        fingerprint=fingerprint,
        source=ACCOUNT_QUOTA_SOURCE_MANUAL,
        status=ACCOUNT_QUOTA_STATUS_DRAFT,
        notes=_clean_optional(payload.notes),
        revision=1,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(item)
    try:
        db.flush()
        _append_history(
            db,
            item=item,
            actor=current_user,
            event_type=ACCOUNT_QUOTA_EVENT_CREATED,
            before=None,
            reason=payload.reason or "创建账户定额",
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_DUPLICATE_FINGERPRINT",
            context={"fingerprint": fingerprint},
        ) from exc
    return item


def create_account_quota_item_from_pricing_draft_sync(
    db: Session,
    current_user: User,
    *,
    item_name: str,
    item_features: str | None,
    spec: str | None,
    unit: str,
    unit_price: Any,
    reason: str,
) -> AccountQuotaItem:
    """Internal-only creation path used by confirmed pricing-draft sync runs.

    Public catalog CRUD remains limited to the trusted ``manual`` source.  The
    caller supplies no account id: tenancy is resolved again on the server.
    """

    account = resolve_current_account(db, current_user, for_update=True)
    cleaned_name = _clean_required(item_name, "item_name")
    cleaned_unit = _clean_required(unit, "unit")
    cleaned_features = _clean_optional(item_features)
    cleaned_spec = _clean_optional(spec)
    fingerprint = build_account_quota_fingerprint(
        item_name=cleaned_name,
        item_features=cleaned_features,
        spec=cleaned_spec,
        unit=cleaned_unit,
    )
    duplicate = _duplicate_query(db, account_id=account.id, fingerprint=fingerprint).with_for_update().one_or_none()
    if duplicate is not None:
        _raise_duplicate(duplicate, fingerprint)
    item = AccountQuotaItem(
        item_uuid=str(uuid.uuid4()),
        account_id=account.id,
        quota_code=None,
        item_name=cleaned_name,
        item_features=cleaned_features,
        spec=cleaned_spec,
        unit=cleaned_unit,
        unit_price=_unit_price(unit_price),
        fingerprint=fingerprint,
        source=ACCOUNT_QUOTA_SOURCE_PRICING_DRAFT_SYNC,
        status=ACCOUNT_QUOTA_STATUS_DRAFT,
        notes=None,
        revision=1,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(item)
    try:
        db.flush()
        _append_history(
            db,
            item=item,
            actor=current_user,
            event_type=ACCOUNT_QUOTA_EVENT_CREATED,
            before=None,
            reason=reason,
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_DUPLICATE_FINGERPRINT",
            context={"fingerprint": fingerprint},
        ) from exc
    return item


def update_account_quota_item_from_pricing_draft_sync(
    db: Session,
    current_user: User,
    item: AccountQuotaItem,
    *,
    expected_revision: int,
    unit_price: Any,
    reason: str,
) -> tuple[AccountQuotaItem, dict[str, Any]]:
    """Apply a deliberate sync price update without changing catalog identity.

    Existing catalog names/features/codes are user-maintained identity data, so
    sync changes only the accepted price.  Any previously active item returns
    to draft and must be explicitly enabled again before a future matching
    phase can read it.
    """

    _check_revision(item, expected_revision)
    _ensure_editable(item)
    before = _snapshot(item)
    item.unit_price = _unit_price(unit_price)
    item.status = ACCOUNT_QUOTA_STATUS_DRAFT
    item.revision = int(item.revision) + 1
    item.updated_by = current_user.id
    db.flush()
    _append_history(
        db,
        item=item,
        actor=current_user,
        event_type=ACCOUNT_QUOTA_EVENT_PRICING_DRAFT_SYNCED,
        before=before,
        reason=reason,
    )
    db.flush()
    return item, before


def snapshot_account_quota_item(item: AccountQuotaItem) -> dict[str, Any]:
    """Stable snapshot for sync-run evidence without exposing private helpers."""

    return _snapshot(item)


def _check_revision(item: AccountQuotaItem, expected_revision: int) -> None:
    if int(item.revision) != int(expected_revision):
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_REVISION_CONFLICT",
            context={
                "expected_revision": int(expected_revision),
                "current_revision": int(item.revision),
            },
        )


def _ensure_editable(item: AccountQuotaItem) -> None:
    if item.status == ACCOUNT_QUOTA_STATUS_ARCHIVED:
        raise AccountQuotaError("ACCOUNT_QUOTA_ARCHIVED")


def update_account_quota_item(
    db: Session,
    current_user: User,
    identifier: str | int,
    payload: AccountQuotaUpdateIn,
) -> AccountQuotaItem:
    item = get_account_quota_item(db, current_user, identifier, for_update=True)
    _check_revision(item, payload.expected_revision)
    _ensure_editable(item)
    before = _snapshot(item)
    supplied = _MUTABLE_FIELDS.intersection(payload.model_fields_set)
    values = {field_name: getattr(payload, field_name) for field_name in supplied}
    if "quota_code" in values:
        item.quota_code = _clean_optional(values["quota_code"])
    if "item_name" in values:
        item.item_name = _clean_required(values["item_name"], "item_name")
    if "item_features" in values:
        item.item_features = _clean_optional(values["item_features"])
    if "spec" in values:
        item.spec = _clean_optional(values["spec"])
    if "unit" in values:
        item.unit = _clean_required(values["unit"], "unit")
    if "unit_price" in values:
        item.unit_price = _unit_price(values["unit_price"])
    if "notes" in values:
        item.notes = _clean_optional(values["notes"])

    fingerprint = build_account_quota_fingerprint(
        item_name=item.item_name,
        item_features=item.item_features,
        spec=item.spec,
        unit=item.unit,
    )
    duplicate = (
        _duplicate_query(db, account_id=item.account_id, fingerprint=fingerprint, exclude_id=item.id)
        .with_for_update()
        .one_or_none()
    )
    if duplicate is not None:
        _raise_duplicate(duplicate, fingerprint)
    item.fingerprint = fingerprint
    item.revision = int(item.revision) + 1
    item.updated_by = current_user.id
    try:
        db.flush()
        _append_history(
            db,
            item=item,
            actor=current_user,
            event_type=ACCOUNT_QUOTA_EVENT_UPDATED,
            before=before,
            reason=payload.reason,
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_DUPLICATE_FINGERPRINT",
            context={"fingerprint": fingerprint},
        ) from exc
    return item


def change_account_quota_status(
    db: Session,
    current_user: User,
    identifier: str | int,
    payload: AccountQuotaStatusIn,
) -> AccountQuotaItem:
    item = get_account_quota_item(db, current_user, identifier, for_update=True)
    _check_revision(item, payload.expected_revision)
    _ensure_editable(item)
    transitions = {
        ACCOUNT_QUOTA_STATUS_DRAFT: {ACCOUNT_QUOTA_STATUS_ACTIVE, ACCOUNT_QUOTA_STATUS_ARCHIVED},
        ACCOUNT_QUOTA_STATUS_ACTIVE: {ACCOUNT_QUOTA_STATUS_DRAFT, ACCOUNT_QUOTA_STATUS_ARCHIVED},
    }
    if payload.target_status not in transitions.get(item.status, set()):
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_INVALID_STATUS_TRANSITION",
            context={"from_status": item.status, "target_status": payload.target_status},
        )
    before = _snapshot(item)
    item.status = payload.target_status
    item.revision = int(item.revision) + 1
    item.updated_by = current_user.id
    db.flush()
    _append_history(
        db,
        item=item,
        actor=current_user,
        event_type=ACCOUNT_QUOTA_EVENT_STATUS_CHANGED,
        before=before,
        reason=payload.reason,
    )
    db.flush()
    return item


def list_account_quota_history(
    db: Session,
    current_user: User,
    identifier: str | int,
    *,
    page: int = 1,
    page_size: int = 100,
) -> tuple[list[AccountQuotaItemHistory], int]:
    item = get_account_quota_item(db, current_user, identifier)
    query = db.query(AccountQuotaItemHistory).filter(
        AccountQuotaItemHistory.account_id == item.account_id,
        AccountQuotaItemHistory.account_quota_item_id == item.id,
    )
    total = query.count()
    rows = (
        query.order_by(AccountQuotaItemHistory.revision.desc(), AccountQuotaItemHistory.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def serialize_account_quota_item(item: AccountQuotaItem) -> dict[str, Any]:
    data = _snapshot(item)
    data.update(
        {
            "created_at": _datetime_text(item.created_at),
            "updated_at": _datetime_text(item.updated_at),
        }
    )
    return data


def serialize_account_quota_history(history: AccountQuotaItemHistory) -> dict[str, Any]:
    return {
        "id": int(history.id),
        "account_quota_item_id": int(history.account_quota_item_id),
        "revision": int(history.revision),
        "event_type": history.event_type,
        "from_status": history.from_status,
        "to_status": history.to_status,
        "before_snapshot": _json_load(history.before_snapshot_json),
        "after_snapshot": _json_load(history.after_snapshot_json),
        "reason": history.reason,
        "actor_id": int(history.actor_id),
        "actor_username": history.actor.username if history.actor is not None else None,
        "created_at": _datetime_text(history.created_at),
    }
