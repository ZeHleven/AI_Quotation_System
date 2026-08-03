from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.enterprise_profile import (
    ENTERPRISE_PROFILE_CATEGORY_VALUES,
    ENTERPRISE_PROFILE_STATUS_ACTIVE,
    ENTERPRISE_PROFILE_STATUS_ARCHIVED,
    ENTERPRISE_PROFILE_STATUS_DRAFT,
    ENTERPRISE_PROFILE_STATUS_VALUES,
    EnterpriseProfileEvent,
    EnterpriseProfileFile,
    EnterpriseProfileItem,
)
from app.models.file_object import FileObject
from app.models.user import User
from app.services.rbac import has_admin_role, has_any_role


ENTERPRISE_PROFILE_VIEW_ROLES = {
    "system_admin",
    "admin",
    "enterprise_profile_viewer",
    "enterprise_profile_editor",
    "enterprise_profile_approver",
}
ENTERPRISE_PROFILE_EDIT_ROLES = {
    "system_admin",
    "admin",
    "enterprise_profile_editor",
    "enterprise_profile_approver",
}
ENTERPRISE_PROFILE_APPROVE_ROLES = {
    "system_admin",
    "admin",
    "enterprise_profile_approver",
}
ENTERPRISE_PROFILE_CANDIDATE_ROLES = ENTERPRISE_PROFILE_VIEW_ROLES | {
    "staff",
    "manager",
    "quote_user",
    "quote_operator",
}

ATTACHMENT_REQUIRED_CATEGORIES = {
    "certificate",
    "qualification",
    "personnel",
    "project_performance",
    "attachment_asset",
}


def can_view_enterprise_profile(user: User) -> bool:
    return has_any_role(user, ENTERPRISE_PROFILE_VIEW_ROLES)


def can_edit_enterprise_profile(user: User) -> bool:
    return has_any_role(user, ENTERPRISE_PROFILE_EDIT_ROLES)


def can_approve_enterprise_profile(user: User) -> bool:
    return has_any_role(user, ENTERPRISE_PROFILE_APPROVE_ROLES)


def can_query_enterprise_profile_candidates(user: User) -> bool:
    return has_any_role(user, ENTERPRISE_PROFILE_CANDIDATE_ROLES)


def require_enterprise_profile_view(user: User) -> None:
    if not can_view_enterprise_profile(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def require_enterprise_profile_edit(user: User) -> None:
    if not can_edit_enterprise_profile(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def require_enterprise_profile_approve(user: User) -> None:
    if not can_approve_enterprise_profile(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def require_enterprise_profile_candidate_access(user: User) -> None:
    if not can_query_enterprise_profile_candidates(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _payload_dict(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    if hasattr(payload, "dict"):
        return payload.dict(exclude_unset=True)
    return dict(payload or {})


def clean_text(value: Any, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned[:max_length] if max_length else cleaned


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    for parser in (
        date.fromisoformat,
        lambda text: datetime.strptime(text, "%Y-%m-%d").date(),
        lambda text: datetime.strptime(text, "%Y/%m/%d").date(),
    ):
        try:
            return parser(raw)
        except ValueError:
            continue
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_DATE")


def dumps_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads_json(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def normalize_category(value: Any) -> str:
    category = clean_text(value, 64) or "other"
    if category not in ENTERPRISE_PROFILE_CATEGORY_VALUES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_CATEGORY")
    return category


def normalize_status(value: Any) -> str:
    status_value = clean_text(value, 24) or ENTERPRISE_PROFILE_STATUS_DRAFT
    if status_value not in ENTERPRISE_PROFILE_STATUS_VALUES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_STATUS")
    return status_value


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_TAGS")
    tags: list[str] = []
    for item in value:
        cleaned = clean_text(item, 64)
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags[:50]


def _validate_date_range(valid_from: date | None, valid_until: date | None) -> None:
    if valid_from and valid_until and valid_until < valid_from:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_DATE_RANGE")


def _normalized_payload(data: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    if not partial or "category" in data:
        normalized["category"] = normalize_category(data.get("category"))
    for field_name, max_length in (
        ("subcategory", 128),
        ("profile_key", 128),
        ("title", 255),
        ("source", 64),
        ("confidentiality", 32),
    ):
        if not partial or field_name in data:
            normalized[field_name] = clean_text(data.get(field_name), max_length)
    for field_name in ("summary", "content_text", "applicable_scope"):
        if not partial or field_name in data:
            normalized[field_name] = clean_text(data.get(field_name))
    if not partial or "structured" in data:
        structured = data.get("structured")
        if structured is not None and not isinstance(structured, dict):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_STRUCTURED")
        normalized["structured_json"] = dumps_json(structured or {})
    if not partial or "tags" in data:
        normalized["tags_json"] = dumps_json(_normalize_tags(data.get("tags")))
    if not partial or "valid_from" in data:
        normalized["valid_from"] = parse_date(data.get("valid_from"))
    if not partial or "valid_until" in data:
        normalized["valid_until"] = parse_date(data.get("valid_until"))

    title = normalized.get("title") if "title" in normalized else None
    if not partial and not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="TITLE_REQUIRED")
    if "source" in normalized:
        normalized["source"] = normalized["source"] or "manual"
    if "confidentiality" in normalized:
        normalized["confidentiality"] = normalized["confidentiality"] or "internal"
    _validate_date_range(normalized.get("valid_from"), normalized.get("valid_until"))
    return normalized


def item_needs_attachment(item: EnterpriseProfileItem) -> bool:
    return item.category in ATTACHMENT_REQUIRED_CATEGORIES


def item_quality_issues(item: EnterpriseProfileItem, *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    issues: list[dict[str, Any]] = []
    attachment_count = len(item.attachments or [])
    has_content = bool(clean_text(item.content_text))
    if item_needs_attachment(item) and attachment_count <= 0:
        issues.append({"code": "missing_attachment", "level": "warning", "message": "attachment required"})
    if not has_content and attachment_count <= 0:
        issues.append({"code": "missing_evidence", "level": "warning", "message": "content or attachment required"})
    if item.valid_until and item.valid_until < today:
        issues.append({"code": "expired", "level": "blocker", "message": "profile item expired"})
    elif item.valid_until and item.valid_until <= today + timedelta(days=30):
        issues.append({"code": "expiring_soon", "level": "warning", "message": "profile item expires within 30 days"})
    return issues


def _record_event(
    db: Session,
    *,
    item: EnterpriseProfileItem,
    event_type: str,
    user: User,
    old_status: str | None = None,
    new_status: str | None = None,
    detail: dict[str, Any] | None = None,
) -> EnterpriseProfileEvent:
    event = EnterpriseProfileEvent(
        event_uuid=str(uuid.uuid4()),
        item_id=item.id,
        event_type=event_type,
        old_status=old_status,
        new_status=new_status,
        detail_json=dumps_json(detail or {}),
        created_by=user.id,
    )
    db.add(event)
    return event


def serialize_attachment(attachment: EnterpriseProfileFile) -> dict[str, Any]:
    file_obj = attachment.file_object
    return {
        "attachment_uuid": attachment.attachment_uuid,
        "file_id": attachment.file_id,
        "attachment_type": attachment.attachment_type,
        "original_filename": attachment.original_filename or (file_obj.original_filename if file_obj else None),
        "description": attachment.description,
        "is_primary": bool(attachment.is_primary),
        "uploaded_by": attachment.uploaded_by,
        "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
    }


def serialize_event(event: EnterpriseProfileEvent) -> dict[str, Any]:
    return {
        "event_uuid": event.event_uuid,
        "event_type": event.event_type,
        "old_status": event.old_status,
        "new_status": event.new_status,
        "detail": loads_json(event.detail_json, {}),
        "created_by": event.created_by,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def serialize_item(item: EnterpriseProfileItem, *, detail: bool = False) -> dict[str, Any]:
    quality_issues = item_quality_issues(item)
    data = {
        "id": item.id,
        "item_uuid": item.item_uuid,
        "category": item.category,
        "subcategory": item.subcategory,
        "profile_key": item.profile_key,
        "title": item.title,
        "summary": item.summary,
        "content_text": item.content_text if detail else None,
        "structured": loads_json(item.structured_json, {}),
        "tags": loads_json(item.tags_json, []),
        "applicable_scope": item.applicable_scope,
        "source": item.source,
        "confidentiality": item.confidentiality,
        "status": item.status,
        "valid_from": item.valid_from.isoformat() if item.valid_from else None,
        "valid_until": item.valid_until.isoformat() if item.valid_until else None,
        "attachment_count": len(item.attachments or []),
        "needs_attachment": item_needs_attachment(item),
        "quality_issues": quality_issues,
        "is_expired": any(issue["code"] == "expired" for issue in quality_issues),
        "is_expiring_soon": any(issue["code"] == "expiring_soon" for issue in quality_issues),
        "created_by": item.created_by,
        "updated_by": item.updated_by,
        "approved_by": item.approved_by,
        "approved_at": item.approved_at.isoformat() if item.approved_at else None,
        "archived_by": item.archived_by,
        "archived_at": item.archived_at.isoformat() if item.archived_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
    if detail:
        data["attachments"] = [serialize_attachment(attachment) for attachment in item.attachments or []]
        data["events"] = [serialize_event(event) for event in item.events or []]
    return data


def get_item_by_uuid(db: Session, item_uuid: str) -> EnterpriseProfileItem:
    item = (
        db.query(EnterpriseProfileItem)
        .options(
            selectinload(EnterpriseProfileItem.attachments).selectinload(EnterpriseProfileFile.file_object),
            selectinload(EnterpriseProfileItem.events),
        )
        .filter(EnterpriseProfileItem.item_uuid == item_uuid)
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ENTERPRISE_PROFILE_ITEM_NOT_FOUND")
    return item


def create_profile_item(db: Session, user: User, payload: Mapping[str, Any] | Any) -> EnterpriseProfileItem:
    require_enterprise_profile_edit(user)
    data = _normalized_payload(_payload_dict(payload))
    item = EnterpriseProfileItem(
        item_uuid=str(uuid.uuid4()),
        status=ENTERPRISE_PROFILE_STATUS_DRAFT,
        created_by=user.id,
        updated_by=user.id,
        **data,
    )
    db.add(item)
    db.flush()
    _record_event(db, item=item, event_type="created", user=user, new_status=item.status)
    return item


def update_profile_item(
    db: Session,
    user: User,
    item: EnterpriseProfileItem,
    payload: Mapping[str, Any] | Any,
) -> EnterpriseProfileItem:
    require_enterprise_profile_edit(user)
    if item.status == ENTERPRISE_PROFILE_STATUS_ARCHIVED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ARCHIVED_ITEM_READONLY")
    data = _normalized_payload(_payload_dict(payload), partial=True)
    old_status = item.status
    for field_name, value in data.items():
        setattr(item, field_name, value)
    item.updated_by = user.id
    _record_event(
        db,
        item=item,
        event_type="updated",
        user=user,
        old_status=old_status,
        new_status=item.status,
        detail={"change_reason": clean_text(_payload_dict(payload).get("change_reason"))},
    )
    return item


def _get_accessible_file(db: Session, user: User, file_id: str) -> FileObject:
    file_obj = db.query(FileObject).filter(FileObject.file_id == file_id).first()
    if not file_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FILE_NOT_FOUND")
    if not has_admin_role(user) and file_obj.username != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FILE_NOT_FOUND")
    return file_obj


def add_profile_attachment(
    db: Session,
    user: User,
    item: EnterpriseProfileItem,
    payload: Mapping[str, Any] | Any,
) -> EnterpriseProfileFile:
    require_enterprise_profile_edit(user)
    if item.status == ENTERPRISE_PROFILE_STATUS_ARCHIVED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ARCHIVED_ITEM_READONLY")
    data = _payload_dict(payload)
    file_id = clean_text(data.get("file_id"), 36)
    if not file_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="FILE_ID_REQUIRED")
    file_obj = _get_accessible_file(db, user, file_id)
    if data.get("is_primary"):
        for attachment in item.attachments or []:
            attachment.is_primary = False
    attachment = EnterpriseProfileFile(
        attachment_uuid=str(uuid.uuid4()),
        item_id=item.id,
        file_id=file_obj.file_id,
        attachment_type=clean_text(data.get("attachment_type"), 64) or "source",
        original_filename=file_obj.original_filename,
        description=clean_text(data.get("description")),
        is_primary=bool(data.get("is_primary")),
        uploaded_by=user.id,
    )
    db.add(attachment)
    item.updated_by = user.id
    _record_event(
        db,
        item=item,
        event_type="attachment_added",
        user=user,
        old_status=item.status,
        new_status=item.status,
        detail={"file_id": file_obj.file_id, "filename": file_obj.original_filename},
    )
    return attachment


def activate_profile_item(db: Session, user: User, item: EnterpriseProfileItem, reason: str) -> EnterpriseProfileItem:
    require_enterprise_profile_approve(user)
    cleaned_reason = clean_text(reason)
    if not cleaned_reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="REASON_REQUIRED")
    if item.status == ENTERPRISE_PROFILE_STATUS_ARCHIVED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ARCHIVED_ITEM_READONLY")
    issues = item_quality_issues(item)
    blocker_codes = {issue["code"] for issue in issues if issue["code"] in {"expired", "missing_evidence", "missing_attachment"}}
    if blocker_codes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ENTERPRISE_PROFILE_QUALITY_BLOCKED", "issues": issues},
        )
    old_status = item.status
    item.status = ENTERPRISE_PROFILE_STATUS_ACTIVE
    item.approved_by = user.id
    item.approved_at = datetime.now(timezone.utc)
    item.updated_by = user.id
    _record_event(
        db,
        item=item,
        event_type="activated",
        user=user,
        old_status=old_status,
        new_status=item.status,
        detail={"reason": cleaned_reason},
    )
    return item


def archive_profile_item(db: Session, user: User, item: EnterpriseProfileItem, reason: str) -> EnterpriseProfileItem:
    require_enterprise_profile_approve(user)
    cleaned_reason = clean_text(reason)
    if not cleaned_reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="REASON_REQUIRED")
    old_status = item.status
    item.status = ENTERPRISE_PROFILE_STATUS_ARCHIVED
    item.archived_by = user.id
    item.archived_at = datetime.now(timezone.utc)
    item.updated_by = user.id
    _record_event(
        db,
        item=item,
        event_type="archived",
        user=user,
        old_status=old_status,
        new_status=item.status,
        detail={"reason": cleaned_reason},
    )
    return item


def list_profile_items(
    db: Session,
    user: User,
    *,
    category: str | None = None,
    status_filter: str | None = None,
    keyword: str | None = None,
    missing_attachment: bool | None = None,
    expiring_days: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[EnterpriseProfileItem], int]:
    require_enterprise_profile_view(user)
    query = db.query(EnterpriseProfileItem).options(selectinload(EnterpriseProfileItem.attachments))
    if category:
        query = query.filter(EnterpriseProfileItem.category == normalize_category(category))
    if status_filter:
        statuses = [normalize_status(item.strip()) for item in status_filter.split(",") if item.strip()]
        if statuses:
            query = query.filter(EnterpriseProfileItem.status.in_(statuses))
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                EnterpriseProfileItem.title.like(like),
                EnterpriseProfileItem.summary.like(like),
                EnterpriseProfileItem.content_text.like(like),
                EnterpriseProfileItem.profile_key.like(like),
                EnterpriseProfileItem.subcategory.like(like),
            )
        )
    if expiring_days is not None:
        deadline = date.today() + timedelta(days=max(0, expiring_days))
        query = query.filter(EnterpriseProfileItem.valid_until.isnot(None), EnterpriseProfileItem.valid_until <= deadline)
    total = query.count()
    items = (
        query.order_by(EnterpriseProfileItem.updated_at.desc(), EnterpriseProfileItem.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    if missing_attachment is not None:
        filtered = [item for item in items if any(issue["code"] == "missing_attachment" for issue in item_quality_issues(item))]
        if missing_attachment:
            items = filtered
        else:
            missing_ids = {item.id for item in filtered}
            items = [item for item in items if item.id not in missing_ids]
    return items, total


def enterprise_profile_summary(db: Session, user: User) -> dict[str, Any]:
    require_enterprise_profile_view(user)
    today = date.today()
    expiring_deadline = today + timedelta(days=30)
    total = db.query(EnterpriseProfileItem).count()
    status_counts = {
        status_value: db.query(EnterpriseProfileItem).filter(EnterpriseProfileItem.status == status_value).count()
        for status_value in ENTERPRISE_PROFILE_STATUS_VALUES
    }
    category_counts = {
        category: db.query(EnterpriseProfileItem).filter(EnterpriseProfileItem.category == category).count()
        for category in sorted(ENTERPRISE_PROFILE_CATEGORY_VALUES)
    }
    expired_count = (
        db.query(EnterpriseProfileItem)
        .filter(EnterpriseProfileItem.valid_until.isnot(None), EnterpriseProfileItem.valid_until < today)
        .count()
    )
    expiring_soon_count = (
        db.query(EnterpriseProfileItem)
        .filter(
            EnterpriseProfileItem.valid_until.isnot(None),
            EnterpriseProfileItem.valid_until >= today,
            EnterpriseProfileItem.valid_until <= expiring_deadline,
        )
        .count()
    )
    sampled_items = (
        db.query(EnterpriseProfileItem)
        .options(selectinload(EnterpriseProfileItem.attachments))
        .order_by(EnterpriseProfileItem.updated_at.desc(), EnterpriseProfileItem.id.desc())
        .limit(500)
        .all()
    )
    missing_attachment_count = sum(
        1 for item in sampled_items if any(issue["code"] == "missing_attachment" for issue in item_quality_issues(item, today=today))
    )
    return {
        "total": total,
        "status_counts": status_counts,
        "category_counts": category_counts,
        "expired_count": expired_count,
        "expiring_soon_count": expiring_soon_count,
        "missing_attachment_count": missing_attachment_count,
        "quality_sample_size": len(sampled_items),
    }


def list_active_profile_candidates(
    db: Session,
    user: User,
    *,
    category: str | None = None,
    keyword: str | None = None,
    limit: int = 20,
) -> list[EnterpriseProfileItem]:
    require_enterprise_profile_candidate_access(user)
    today = date.today()
    query = (
        db.query(EnterpriseProfileItem)
        .options(selectinload(EnterpriseProfileItem.attachments))
        .filter(EnterpriseProfileItem.status == ENTERPRISE_PROFILE_STATUS_ACTIVE)
        .filter(or_(EnterpriseProfileItem.valid_until.is_(None), EnterpriseProfileItem.valid_until >= today))
    )
    if category:
        query = query.filter(EnterpriseProfileItem.category == normalize_category(category))
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                EnterpriseProfileItem.title.like(like),
                EnterpriseProfileItem.summary.like(like),
                EnterpriseProfileItem.content_text.like(like),
                EnterpriseProfileItem.profile_key.like(like),
                EnterpriseProfileItem.subcategory.like(like),
                EnterpriseProfileItem.tags_json.like(like),
            )
        )
    return query.order_by(EnterpriseProfileItem.updated_at.desc(), EnterpriseProfileItem.id.desc()).limit(limit).all()
