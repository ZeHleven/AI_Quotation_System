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
from app.schemas.account_quota import (
    AccountQuotaBatchStatusIn,
    AccountQuotaCreateIn,
    AccountQuotaStatusIn,
    AccountQuotaUpdateIn,
)
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
_DETAIL_TYPES = {"process", "material", "subcontract"}
_DETAIL_NOTE_SCHEMA = "account_quota_detail_v1"
_PROCESS_VERBS = (
    "安装",
    "拆除",
    "处理",
    "砌筑",
    "铺贴",
    "铺装",
    "涂刷",
    "修复",
    "施工",
    "制作",
    "找平",
    "开槽",
    "开孔",
    "清运",
    "搬运",
    "打磨",
    "焊接",
)
_PROCESS_CONTEXT_KEYWORDS = ("人工", "工序", "劳务")
_PROCESS_METHOD_KEYWORDS = (
    "抹灰",
    "铺贴",
    "粘贴",
    "挂贴",
    "干挂",
    "湿贴",
    "找平",
    "凿毛",
    "贴膜",
    "开槽",
    "修复",
    "回填",
    "砌筑",
    "清运",
    "外运",
    "保洁",
    "收口",
    "保护层",
)
_SUBCONTRACT_STRONG_KEYWORDS = (
    "分包",
    "外协",
    "定制",
    "成品",
    "半成品",
)
_SUBCONTRACT_DELIVERABLE_KEYWORDS = (
    "玻璃门",
    "木饰面门",
    "木饰面",
    "钢化玻璃",
    "铝扣板",
    "门",
    "窗",
    "柜",
    "栏杆",
    "扶手",
    "台面",
    "隔断",
    "吊顶天棚",
    "天花吊顶",
    "造型吊顶",
)
_MATERIAL_OBJECT_KEYWORDS = (
    "配电箱",
    "接线箱",
    "脚手架",
    "桥架",
    "电线管",
    "塑料电线管",
    "水管",
    "风管",
    "线管",
    "电气配线",
    "电力电缆",
    "电缆",
    "灯具",
    "射灯",
    "灯带",
    "条形灯",
    "吊灯",
    "荧光灯",
    "开关",
    "插座",
    "控制器",
    "感应开关",
    "阀",
    "水表",
    "地漏",
    "坐便器",
    "蹲便器",
    "小便器",
    "马桶",
    "水槽",
    "水龙头",
    "小厨宝",
    "纸巾架",
    "纸巾盒",
)
_MATERIAL_KEYWORDS = (
    "涂料",
    "乳胶漆",
    "腻子",
    "美缝剂",
    "药剂",
    "陶粒",
    "龙骨",
    "石材",
    "瓷砖",
    "地砖",
    "墙纸",
    "壁纸",
    "砂浆",
    "水泥",
    "胶",
    "线管",
    "电线",
    "阀门",
    "灯具",
    "开关",
    "插座",
    "阻燃板",
    "石膏板",
    "水泥板",
    "基层板",
    "板材",
)
_MATERIAL_UNITS = {"kg", "公斤", "吨", "t", "张", "块", "根", "卷", "桶", "袋", "支", "瓶"}


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


def _validate_detail_type(detail_type: str | None) -> str | None:
    cleaned = _clean_optional(detail_type)
    if not cleaned:
        return None
    if cleaned not in _DETAIL_TYPES:
        raise AccountQuotaError(
            "ACCOUNT_QUOTA_DETAIL_TYPE_INVALID",
            status_code=422,
            context={"detail_type": cleaned},
        )
    return cleaned


def _parse_detail_notes(notes: str | None) -> tuple[str | None, dict[str, Any], str | None]:
    parsed = _json_load(notes)
    if isinstance(parsed, dict):
        raw_type = str(parsed.get("detail_type") or "").strip()
        detail_type = raw_type if raw_type in _DETAIL_TYPES else None
        extra = {key: value for key, value in parsed.items() if key not in {"schema", "detail_type", "legacy_note"}}
        legacy_note = _clean_optional(parsed.get("legacy_note"))
        return detail_type, extra, legacy_note
    return None, {}, _clean_optional(notes)


def _decimal_or_zero(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return parsed if parsed.is_finite() else Decimal("0")


def _q6(value: Decimal) -> Decimal:
    return value.quantize(_Q6, rounding=ROUND_HALF_UP)


def _money_text(value: Decimal) -> str:
    return format(_q6(value), "f")


def _has_subcontract_fee_split(extra: dict[str, Any]) -> bool:
    keys = ("labor_fee", "main_material_fee", "auxiliary_material_fee")
    if not all(key in extra for key in keys):
        return False
    return sum((_decimal_or_zero(extra.get(key)) for key in keys), Decimal("0")) > 0


def _is_process_text(text: str) -> bool:
    stripped = text.strip()
    if any(stripped.startswith(verb) or stripped.endswith(verb) for verb in _PROCESS_VERBS):
        return True
    return any(keyword in stripped for keyword in _PROCESS_CONTEXT_KEYWORDS + _PROCESS_METHOD_KEYWORDS)


def _is_material_text(name: str, text: str, unit: str | None) -> bool:
    if any(keyword in name for keyword in _MATERIAL_OBJECT_KEYWORDS):
        return True
    if _is_process_text(name):
        return False
    normalized_unit = str(unit or "").strip().lower()
    if normalized_unit in _MATERIAL_UNITS:
        return True
    if any(keyword in name for keyword in _MATERIAL_KEYWORDS):
        return True
    return any(keyword in text for keyword in _MATERIAL_KEYWORDS) and not any(
        keyword in name for keyword in _PROCESS_METHOD_KEYWORDS
    )


def _infer_detail_type_from_text(item: AccountQuotaItem, extra: dict[str, Any] | None = None) -> str:
    extra = extra or {}
    if _has_subcontract_fee_split(extra):
        return "subcontract"
    name = str(item.item_name or "")
    text = f"{name} {item.item_features or ''} {item.spec or ''} {item.notes or ''}"
    if any(keyword in text for keyword in _SUBCONTRACT_STRONG_KEYWORDS):
        return "subcontract"
    if _is_material_text(name, text, item.unit):
        return "material"
    if _is_process_text(name) or any(keyword in text for keyword in _PROCESS_CONTEXT_KEYWORDS):
        return "process"
    if any(keyword in text for keyword in _SUBCONTRACT_DELIVERABLE_KEYWORDS):
        return "subcontract"
    return "process"


def _detail_type_for_item(item: AccountQuotaItem) -> str:
    explicit, extra, _legacy_note = _parse_detail_notes(item.notes)
    return explicit or _infer_detail_type_from_text(item, extra)


def _subcontract_split_ratios(item: AccountQuotaItem) -> tuple[Decimal, Decimal, Decimal]:
    text = f"{item.item_name or ''} {item.item_features or ''} {item.spec or ''}"
    if "玻璃" in text:
        return Decimal("0.15"), Decimal("0.78"), Decimal("0.07")
    if "门" in text or "窗" in text:
        return Decimal("0.18"), Decimal("0.74"), Decimal("0.08")
    if "木饰面" in text or "铝扣板" in text:
        return Decimal("0.20"), Decimal("0.72"), Decimal("0.08")
    return Decimal("0.25"), Decimal("0.65"), Decimal("0.10")


def _ensure_subcontract_fee_split(item: AccountQuotaItem, extra: dict[str, Any]) -> dict[str, Any]:
    if _has_subcontract_fee_split(extra):
        labor = _decimal_or_zero(extra.get("labor_fee"))
        main = _decimal_or_zero(extra.get("main_material_fee"))
        auxiliary = _decimal_or_zero(extra.get("auxiliary_material_fee"))
        total = _q6(labor + main + auxiliary)
        unit_price = _unit_price(item.unit_price)
        if total == unit_price:
            return extra
        if total > 0:
            adjusted = dict(extra)
            calibrated_labor = _q6(labor * unit_price / total)
            calibrated_main = _q6(main * unit_price / total)
            calibrated_auxiliary = _q6(unit_price - calibrated_labor - calibrated_main)
            adjusted.update(
                {
                    "labor_fee": _money_text(calibrated_labor),
                    "main_material_fee": _money_text(calibrated_main),
                    "auxiliary_material_fee": _money_text(calibrated_auxiliary),
                    "subcontract_breakdown_total": _money_text(unit_price),
                    "subcontract_breakdown_source": "manual_split_calibrated",
                    "subcontract_breakdown_note": "已有三费拆分已按分包单价等比例校准，三费合计等于分包单价。",
                }
            )
            return adjusted

    unit_price = _unit_price(item.unit_price)
    labor_ratio, main_ratio, _auxiliary_ratio = _subcontract_split_ratios(item)
    labor = _q6(unit_price * labor_ratio)
    main = _q6(unit_price * main_ratio)
    auxiliary = _q6(unit_price - labor - main)
    adjusted = dict(extra)
    adjusted.update(
        {
            "labor_fee": _money_text(labor),
            "main_material_fee": _money_text(main),
            "auxiliary_material_fee": _money_text(auxiliary),
            "subcontract_breakdown_total": _money_text(labor + main + auxiliary),
            "subcontract_breakdown_source": "rule_estimate_pending_llm",
            "subcontract_breakdown_note": "按分包单价规则拆分，三费合计已校准为分包单价，建议后续用LLM或人工复核。",
        }
    )
    return adjusted


def _detail_extra_for_item(item: AccountQuotaItem, detail_type: str, extra: dict[str, Any]) -> dict[str, Any]:
    if detail_type == "subcontract":
        return _ensure_subcontract_fee_split(item, extra)
    return extra


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
    detail_type: str | None = None,
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
    cleaned_detail_type = _validate_detail_type(detail_type)
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
                AccountQuotaItem.notes.like(pattern),
            )
        )
    ordered_query = query.order_by(AccountQuotaItem.updated_at.desc(), AccountQuotaItem.id.desc())
    if cleaned_detail_type:
        matched = [item for item in ordered_query.all() if _detail_type_for_item(item) == cleaned_detail_type]
        total = len(matched)
        start = (page - 1) * page_size
        rows = matched[start : start + page_size]
    else:
        total = query.count()
        rows = ordered_query.offset((page - 1) * page_size).limit(page_size).all()
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
    notes: str | None = None,
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
        notes=_clean_optional(notes),
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
    notes: str | None = None,
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
    if notes is not None:
        item.notes = _clean_optional(notes)
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


def batch_change_account_quota_status(
    db: Session,
    current_user: User,
    payload: AccountQuotaBatchStatusIn,
) -> list[AccountQuotaItem]:
    changed_items: list[AccountQuotaItem] = []
    for entry in payload.items:
        item = change_account_quota_status(
            db,
            current_user,
            entry.item_identifier.strip(),
            AccountQuotaStatusIn(
                target_status=payload.target_status,
                expected_revision=entry.expected_revision,
                reason=payload.reason,
            ),
        )
        changed_items.append(item)
    return changed_items


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
    detail_type, detail_extra, legacy_note = _parse_detail_notes(item.notes)
    resolved_detail_type = detail_type or _infer_detail_type_from_text(item, detail_extra)
    resolved_detail_extra = _detail_extra_for_item(item, resolved_detail_type, detail_extra)
    data.update(
        {
            "detail_type": resolved_detail_type,
            "detail_extra": resolved_detail_extra,
            "legacy_note": legacy_note,
            "detail_note_schema": _DETAIL_NOTE_SCHEMA if resolved_detail_extra else None,
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
