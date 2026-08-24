"""Phase 4D-2 governed import of real enterprise capability evidence files.

Files are content-addressed and never parsed here.  Slot assignment is an
explicit reviewer command; filename, MIME type, OCR, and models are not allowed
to infer I01-I11 mappings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import uuid
from typing import Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bid_assessment_release import (
    BidEnterpriseEvidenceItem,
    BidEnterpriseEvidencePackage,
    BidEnterpriseEvidencePackageItem,
)
from app.services.bid_assessment_eventing import append_audit_log, as_utc, canonical_hash
from app.services.bid_enterprise_capability import ENTERPRISE_SLOT_CODES
from app.services.bid_upload_file_storage import (
    BidUploadObjectStorage,
    get_bid_upload_object_storage,
)


EVIDENCE_ITEM_SCHEMA = "bid.enterprise.evidence-item.v1"
EVIDENCE_PACKAGE_SCHEMA = "bid.enterprise.evidence-package.v1"
EVIDENCE_PACKAGE_VALIDATION_SCHEMA = "bid.enterprise.evidence-package-validation.v1"
EVIDENCE_IMPORT_AUTHORITY = "bid-enterprise-evidence-import-authority-v1"
EVIDENCE_OBJECT_PREFIX = "bid-enterprise-evidence/v1"
FILE_EVIDENCE_CLASSES = frozenset(
    {"official_document", "internal_system", "audited_record"}
)
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_ITEM_NAMESPACE = uuid.UUID("ad07cbcb-c02a-46a8-806e-713a53359910")
_PACKAGE_NAMESPACE = uuid.UUID("86223e6e-24aa-4fc3-b3ed-d5f37b25d8f5")


class BidEnterpriseEvidenceImportError(RuntimeError):
    code = "BID_ENTERPRISE_EVIDENCE_IMPORT_ERROR"


@dataclass(frozen=True)
class ImportedEnterpriseEvidenceItemResult:
    item: BidEnterpriseEvidenceItem
    created: bool
    projection: dict[str, Any]


@dataclass(frozen=True)
class FrozenEnterpriseEvidencePackageResult:
    package: BidEnterpriseEvidencePackage
    created: bool
    projection: dict[str, Any]


def _database_utc_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "mysql":
        value = db.execute(select(func.utc_timestamp(6))).scalar_one()
    else:
        value = db.execute(select(func.current_timestamp())).scalar_one()
    if not isinstance(value, datetime):
        raise BidEnterpriseEvidenceImportError("BID_DATABASE_TIME_INVALID")
    return as_utc(value)


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any, *, field: str) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        return as_utc(parsed)
    except (TypeError, ValueError) as exc:
        raise BidEnterpriseEvidenceImportError(
            f"BID_ENTERPRISE_EVIDENCE_{field.upper()}_INVALID"
        ) from exc


def _normalize_text(value: Any, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum:
        raise BidEnterpriseEvidenceImportError(
            f"BID_ENTERPRISE_EVIDENCE_{field.upper()}_INVALID"
        )
    return normalized


def _normalize_identity(value: Any, *, field: str, maximum: int) -> str:
    normalized = _normalize_text(value, field=field, maximum=maximum)
    if not _IDENTITY_PATTERN.fullmatch(normalized):
        raise BidEnterpriseEvidenceImportError(
            f"BID_ENTERPRISE_EVIDENCE_{field.upper()}_INVALID"
        )
    return normalized


def _item_object_key(content_sha256: str) -> str:
    return (
        f"{EVIDENCE_OBJECT_PREFIX}/{content_sha256[:2]}/"
        f"{content_sha256}"
    )


def _item_projection(row: BidEnterpriseEvidenceItem) -> dict[str, Any]:
    return {
        "schema": EVIDENCE_ITEM_SCHEMA,
        "evidence_item_id": str(row.id),
        "status": str(row.status),
        "evidence_class": str(row.evidence_class),
        "source_record_id": str(row.source_record_id),
        "source_version": str(row.source_version),
        "source_label": str(row.source_label),
        "evidence_ref": f"{row.source_record_id}@{row.source_version}",
        "original_filename": str(row.original_filename),
        "mime_type": str(row.mime_type),
        "size_bytes": int(row.size_bytes),
        "content_sha256": str(row.content_sha256),
        "item_hash": str(row.item_hash),
        "valid_from": _utc_text(row.valid_from),
        "valid_to": _utc_text(row.valid_to),
        "uploaded_at": _utc_text(row.uploaded_at),
    }


def list_enterprise_evidence_items(
    db: Session,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = (
        db.query(BidEnterpriseEvidenceItem)
        .filter(BidEnterpriseEvidenceItem.status == "frozen")
        .order_by(
            BidEnterpriseEvidenceItem.uploaded_at.desc(),
            BidEnterpriseEvidenceItem.id.desc(),
        )
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    return [_item_projection(row) for row in rows]


def import_enterprise_evidence_item(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
    file_stream: BinaryIO,
    inspection: Any,
    request_id: str,
    storage: BidUploadObjectStorage | None = None,
    now: datetime | None = None,
) -> ImportedEnterpriseEvidenceItemResult:
    evidence_class = str(command.get("evidence_class") or "")
    if evidence_class not in FILE_EVIDENCE_CLASSES:
        raise BidEnterpriseEvidenceImportError(
            "BID_ENTERPRISE_EVIDENCE_CLASS_INVALID"
        )
    source_record_id = _normalize_identity(
        command.get("source_record_id"), field="source_record_id", maximum=128
    )
    source_version = _normalize_identity(
        command.get("source_version"), field="source_version", maximum=64
    )
    source_label = _normalize_text(
        command.get("source_label"), field="source_label", maximum=300
    )
    valid_from = _parse_time(command.get("valid_from"), field="valid_from")
    valid_to = _parse_time(command.get("valid_to"), field="valid_to")
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise BidEnterpriseEvidenceImportError(
            "BID_ENTERPRISE_EVIDENCE_VALIDITY_INVALID"
        )
    content_sha256 = str(getattr(inspection, "sha256", "") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
        raise BidEnterpriseEvidenceImportError("BID_ENTERPRISE_EVIDENCE_HASH_INVALID")
    filename = _normalize_text(
        getattr(inspection, "filename", ""),
        field="filename",
        maximum=500,
    )
    mime_type = _normalize_text(
        getattr(inspection, "canonical_mime_type", ""),
        field="mime_type",
        maximum=160,
    )
    size_bytes = int(getattr(inspection, "size_bytes", 0) or 0)
    if size_bytes < 1:
        raise BidEnterpriseEvidenceImportError("BID_ENTERPRISE_EVIDENCE_SIZE_INVALID")

    item_payload = {
        "schema": EVIDENCE_ITEM_SCHEMA,
        "authority_version": EVIDENCE_IMPORT_AUTHORITY,
        "evidence_class": evidence_class,
        "source_record_id": source_record_id,
        "source_version": source_version,
        "source_label": source_label,
        "original_filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "content_sha256": content_sha256,
        "valid_from": _utc_text(valid_from),
        "valid_to": _utc_text(valid_to),
    }
    item_hash = canonical_hash(item_payload)
    existing = (
        db.query(BidEnterpriseEvidenceItem)
        .filter(BidEnterpriseEvidenceItem.item_hash == item_hash)
        .one_or_none()
    )
    if existing is not None:
        return ImportedEnterpriseEvidenceItemResult(
            item=existing,
            created=False,
            projection=_item_projection(existing),
        )
    source_version_existing = (
        db.query(BidEnterpriseEvidenceItem)
        .filter(
            BidEnterpriseEvidenceItem.source_record_id == source_record_id,
            BidEnterpriseEvidenceItem.source_version == source_version,
            BidEnterpriseEvidenceItem.content_sha256 == content_sha256,
        )
        .one_or_none()
    )
    if source_version_existing is not None:
        # The same source version and bytes cannot be silently re-labelled.  A
        # caller must either replay the exact immutable command or mint a new
        # source version.
        raise BidEnterpriseEvidenceImportError(
            "BID_ENTERPRISE_EVIDENCE_SOURCE_VERSION_CONFLICT"
        )

    object_ref = _item_object_key(content_sha256)
    object_storage = storage or get_bid_upload_object_storage()
    object_storage.put(
        stream=file_stream,
        object_key=object_ref,
        size_bytes=size_bytes,
        mime_type=mime_type,
    )
    current_time = as_utc(now) if now is not None else _database_utc_now(db)
    item_id = str(uuid.uuid5(_ITEM_NAMESPACE, item_hash))
    row = BidEnterpriseEvidenceItem(
        id=item_id,
        status="frozen",
        evidence_class=evidence_class,
        source_record_id=source_record_id,
        source_version=source_version,
        source_label=source_label,
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        content_sha256=content_sha256,
        item_hash=item_hash,
        object_ref=object_ref,
        valid_from=valid_from,
        valid_to=valid_to,
        uploaded_by=int(actor_id),
        uploaded_at=current_time,
        created_at=current_time,
    )
    db.add(row)
    db.flush()
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=f"user:{actor_id}",
        action="bid.enterprise_evidence_item.import",
        entity_type="enterprise_evidence_item",
        entity_id=item_id,
        outcome="succeeded",
        request_id=request_id,
        after={
            "item_hash": item_hash,
            "content_sha256": content_sha256,
            "source_record_id": source_record_id,
            "source_version": source_version,
        },
        metadata={"authority_version": EVIDENCE_IMPORT_AUTHORITY},
        occurred_at=current_time,
    )
    return ImportedEnterpriseEvidenceItemResult(
        item=row,
        created=True,
        projection=_item_projection(row),
    )


def _normalize_package_slots(command: dict[str, Any]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for raw in command.get("slots") or []:
        try:
            item = dict(raw)
        except (TypeError, ValueError) as exc:
            raise BidEnterpriseEvidenceImportError(
                "BID_ENTERPRISE_EVIDENCE_SLOT_INVALID"
            ) from exc
        code = str(item.get("slot_code") or "")
        if code not in ENTERPRISE_SLOT_CODES:
            raise BidEnterpriseEvidenceImportError(
                "BID_ENTERPRISE_EVIDENCE_SLOT_INVALID"
            )
        item_ids = sorted({str(value).strip() for value in item.get("evidence_item_ids") or []})
        if len(item_ids) > 20 or any(
            not _IDENTITY_PATTERN.fullmatch(value) or len(value) > 80
            for value in item_ids
        ):
            raise BidEnterpriseEvidenceImportError(
                "BID_ENTERPRISE_EVIDENCE_ITEM_SET_INVALID"
            )
        note = str(item.get("note") or "").strip() or None
        if note is not None and len(note) > 1000:
            raise BidEnterpriseEvidenceImportError(
                "BID_ENTERPRISE_EVIDENCE_SLOT_NOTE_INVALID"
            )
        if not item_ids and not note:
            raise BidEnterpriseEvidenceImportError(
                "BID_ENTERPRISE_EVIDENCE_UNMAPPED_NOTE_REQUIRED"
            )
        slots.append(
            {"slot_code": code, "evidence_item_ids": item_ids, "note": note}
        )
    slots.sort(key=lambda item: item["slot_code"])
    if [item["slot_code"] for item in slots] != list(ENTERPRISE_SLOT_CODES):
        raise BidEnterpriseEvidenceImportError(
            "BID_ENTERPRISE_EVIDENCE_SLOT_SET_INCOMPLETE"
        )
    return slots


def _stable_package_slots(
    projected_slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the replay-stable subset committed by Candidate/Package hashes."""

    return [
        {
            "slot_code": str(slot["slot_code"]),
            "evidence_items": [
                {
                    "evidence_item_id": str(item["evidence_item_id"]),
                    "item_hash": str(item["item_hash"]),
                    "content_sha256": str(item["content_sha256"]),
                    "evidence_class": str(item["evidence_class"]),
                    "source_record_id": str(item["source_record_id"]),
                    "source_version": str(item["source_version"]),
                    "valid_from": item.get("valid_from"),
                    "valid_to": item.get("valid_to"),
                }
                for item in slot["evidence_items"]
            ],
            "note": slot.get("note"),
            "coverage_status": str(slot["coverage_status"]),
        }
        for slot in projected_slots
    ]


def preview_enterprise_evidence_package(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
) -> dict[str, Any]:
    current_time = _database_utc_now(db)
    as_of = _parse_time(command.get("as_of"), field="package_as_of")
    if as_of is None or as_of > current_time + timedelta(minutes=5):
        raise BidEnterpriseEvidenceImportError(
            "BID_ENTERPRISE_EVIDENCE_PACKAGE_AS_OF_INVALID"
        )
    package_label = _normalize_text(
        command.get("package_label"), field="package_label", maximum=300
    )
    change_note = _normalize_text(
        command.get("change_note"), field="change_note", maximum=2000
    )
    slots = _normalize_package_slots(command)
    selected_ids = sorted(
        {
            item_id
            for slot in slots
            for item_id in slot["evidence_item_ids"]
        }
    )
    rows = (
        db.query(BidEnterpriseEvidenceItem)
        .filter(BidEnterpriseEvidenceItem.id.in_(selected_ids))
        .all()
        if selected_ids
        else []
    )
    row_by_id = {str(row.id): row for row in rows}
    blockers: list[str] = []
    follow_ups: list[str] = []
    if not selected_ids:
        blockers.append("EVIDENCE_PACKAGE_EMPTY")
    item_projections: dict[str, dict[str, Any]] = {}
    for item_id in selected_ids:
        row = row_by_id.get(item_id)
        if row is None or str(row.status) != "frozen":
            blockers.append(f"EVIDENCE_ITEM_NOT_FOUND:{item_id}")
            continue
        if row.valid_from is not None and as_utc(row.valid_from) > as_of:
            blockers.append(f"EVIDENCE_ITEM_NOT_YET_VALID:{item_id}")
        if row.valid_to is not None and as_utc(row.valid_to) < as_of:
            blockers.append(f"EVIDENCE_ITEM_EXPIRED:{item_id}")
        item_projections[item_id] = _item_projection(row)

    projected_slots: list[dict[str, Any]] = []
    for slot in slots:
        mapped = [
            item_projections[item_id]
            for item_id in slot["evidence_item_ids"]
            if item_id in item_projections
        ]
        if not slot["evidence_item_ids"]:
            follow_ups.append(f"{slot['slot_code']}_EVIDENCE_UNAVAILABLE")
        projected_slots.append(
            {
                "slot_code": slot["slot_code"],
                "evidence_item_ids": list(slot["evidence_item_ids"]),
                "evidence_items": mapped,
                "note": slot["note"],
                "coverage_status": "mapped" if mapped else "unknown",
            }
        )
    stable_slots = _stable_package_slots(projected_slots)
    candidate_hash_payload = {
        "schema": EVIDENCE_PACKAGE_VALIDATION_SCHEMA,
        "authority_version": EVIDENCE_IMPORT_AUTHORITY,
        "package_label": package_label,
        "as_of": _utc_text(as_of),
        "change_note": change_note,
        "slots": stable_slots,
    }
    candidate_hash = canonical_hash(candidate_hash_payload)
    existing = (
        db.query(BidEnterpriseEvidencePackage)
        .filter(BidEnterpriseEvidencePackage.candidate_hash == candidate_hash)
        .one_or_none()
    )
    return {
        "schema": EVIDENCE_PACKAGE_VALIDATION_SCHEMA,
        "authority_version": EVIDENCE_IMPORT_AUTHORITY,
        "package_label": package_label,
        "as_of": _utc_text(as_of),
        "change_note": change_note,
        "slots": projected_slots,
        "reviewer_id": int(actor_id),
        "candidate_hash": candidate_hash,
        "blocking_codes": sorted(set(blockers)),
        "follow_up_codes": sorted(set(follow_ups)),
        "can_freeze": not blockers,
        "already_frozen": existing is not None,
        "existing_package": (
            project_enterprise_evidence_package(db, existing)
            if existing is not None
            else None
        ),
    }


def project_enterprise_evidence_package(
    db: Session,
    row: BidEnterpriseEvidencePackage,
) -> dict[str, Any]:
    mappings = (
        db.query(BidEnterpriseEvidencePackageItem, BidEnterpriseEvidenceItem)
        .join(
            BidEnterpriseEvidenceItem,
            BidEnterpriseEvidenceItem.id
            == BidEnterpriseEvidencePackageItem.evidence_item_id,
        )
        .filter(BidEnterpriseEvidencePackageItem.package_id == row.id)
        .order_by(
            BidEnterpriseEvidencePackageItem.slot_code.asc(),
            BidEnterpriseEvidencePackageItem.evidence_item_id.asc(),
        )
        .all()
    )
    by_slot: dict[str, list[dict[str, Any]]] = {
        code: [] for code in ENTERPRISE_SLOT_CODES
    }
    for mapping, item in mappings:
        by_slot[str(mapping.slot_code)].append(_item_projection(item))
    manifest = dict(row.manifest_json or {})
    notes = {
        str(slot.get("slot_code")): slot.get("note")
        for slot in manifest.get("slots") or []
        if isinstance(slot, dict)
    }
    return {
        "schema": EVIDENCE_PACKAGE_SCHEMA,
        "evidence_package_id": str(row.id),
        "version": str(row.version),
        "status": str(row.status),
        "package_label": str(row.package_label),
        "change_note": str(row.change_note),
        "as_of": _utc_text(row.as_of),
        "slots": [
            {
                "slot_code": code,
                "evidence_items": by_slot[code],
                "note": notes.get(code),
                "coverage_status": "mapped" if by_slot[code] else "unknown",
            }
            for code in ENTERPRISE_SLOT_CODES
        ],
        "candidate_hash": str(row.candidate_hash),
        "package_hash": str(row.package_hash),
        "frozen_at": _utc_text(row.frozen_at),
    }


def latest_enterprise_evidence_package(db: Session) -> dict[str, Any] | None:
    row = (
        db.query(BidEnterpriseEvidencePackage)
        .filter(BidEnterpriseEvidencePackage.status == "frozen")
        .order_by(
            BidEnterpriseEvidencePackage.as_of.desc(),
            BidEnterpriseEvidencePackage.frozen_at.desc(),
            BidEnterpriseEvidencePackage.id.desc(),
        )
        .first()
    )
    return None if row is None else project_enterprise_evidence_package(db, row)


def validate_enterprise_evidence_package_at(
    db: Session,
    *,
    package_id: str,
    expected_package_hash: str,
    effective_at: datetime,
) -> BidEnterpriseEvidencePackage | None:
    row = (
        db.query(BidEnterpriseEvidencePackage)
        .filter(
            BidEnterpriseEvidencePackage.id == package_id,
            BidEnterpriseEvidencePackage.status == "frozen",
        )
        .one_or_none()
    )
    if row is None or str(row.package_hash) != str(expected_package_hash):
        return None
    current_time = as_utc(effective_at)
    items = (
        db.query(BidEnterpriseEvidenceItem)
        .join(
            BidEnterpriseEvidencePackageItem,
            BidEnterpriseEvidencePackageItem.evidence_item_id
            == BidEnterpriseEvidenceItem.id,
        )
        .filter(BidEnterpriseEvidencePackageItem.package_id == package_id)
        .all()
    )
    if not items or any(
        str(item.status) != "frozen"
        or (item.valid_from is not None and as_utc(item.valid_from) > current_time)
        or (item.valid_to is not None and as_utc(item.valid_to) < current_time)
        for item in items
    ):
        return None
    return row


def freeze_enterprise_evidence_package(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
    request_id: str,
    expected_candidate_hash: str,
    now: datetime | None = None,
) -> FrozenEnterpriseEvidencePackageResult:
    preview = preview_enterprise_evidence_package(
        db,
        actor_id=actor_id,
        command=command,
    )
    normalized_expected = str(expected_candidate_hash or "").strip().lower()
    if normalized_expected != str(preview["candidate_hash"]):
        raise BidEnterpriseEvidenceImportError(
            "BID_ENTERPRISE_EVIDENCE_CANDIDATE_HASH_MISMATCH"
        )
    existing = (
        db.query(BidEnterpriseEvidencePackage)
        .filter(BidEnterpriseEvidencePackage.candidate_hash == normalized_expected)
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        return FrozenEnterpriseEvidencePackageResult(
            package=existing,
            created=False,
            projection=project_enterprise_evidence_package(db, existing),
        )
    if not preview["can_freeze"]:
        raise BidEnterpriseEvidenceImportError(
            "BID_ENTERPRISE_EVIDENCE_PACKAGE_NOT_READY"
        )
    current_time = as_utc(now) if now is not None else _database_utc_now(db)
    package_id = str(uuid.uuid5(_PACKAGE_NAMESPACE, normalized_expected))
    version = f"enterprise-evidence-{current_time:%Y%m%d%H%M%S}-{normalized_expected[:12]}"
    manifest = {
        "schema": EVIDENCE_PACKAGE_SCHEMA,
        "authority_version": EVIDENCE_IMPORT_AUTHORITY,
        "evidence_package_id": package_id,
        "version": version,
        "package_label": str(preview["package_label"]),
        "as_of": str(preview["as_of"]),
        "slots": _stable_package_slots(list(preview["slots"])),
        "candidate_hash": normalized_expected,
    }
    package_hash = canonical_hash(manifest)
    row = BidEnterpriseEvidencePackage(
        id=package_id,
        version=version,
        status="frozen",
        package_label=str(preview["package_label"]),
        change_note=str(preview["change_note"]),
        as_of=datetime.fromisoformat(str(preview["as_of"]).replace("Z", "+00:00")),
        manifest_json=manifest,
        candidate_hash=normalized_expected,
        package_hash=package_hash,
        frozen_by=int(actor_id),
        frozen_at=current_time,
        created_at=current_time,
    )
    db.add(row)
    for slot in preview["slots"]:
        for item in slot["evidence_items"]:
            mapping_id = str(
                uuid.uuid5(
                    _PACKAGE_NAMESPACE,
                    f"{package_id}:{slot['slot_code']}:{item['evidence_item_id']}",
                )
            )
            db.add(
                BidEnterpriseEvidencePackageItem(
                    id=mapping_id,
                    package_id=package_id,
                    evidence_item_id=str(item["evidence_item_id"]),
                    slot_code=str(slot["slot_code"]),
                    mapping_note=slot.get("note"),
                    created_at=current_time,
                )
            )
    db.flush()
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=f"user:{actor_id}",
        action="bid.enterprise_evidence_package.freeze",
        entity_type="enterprise_evidence_package",
        entity_id=package_id,
        outcome="succeeded",
        request_id=request_id,
        after={
            "version": version,
            "package_hash": package_hash,
            "candidate_hash": normalized_expected,
        },
        metadata={"authority_version": EVIDENCE_IMPORT_AUTHORITY},
        occurred_at=current_time,
    )
    return FrozenEnterpriseEvidencePackageResult(
        package=row,
        created=True,
        projection=project_enterprise_evidence_package(db, row),
    )


def validate_package_item_reference(
    db: Session,
    *,
    package_id: str,
    slot_code: str,
    evidence_item_id: str,
) -> tuple[BidEnterpriseEvidencePackage, BidEnterpriseEvidenceItem]:
    pair = (
        db.query(BidEnterpriseEvidencePackage, BidEnterpriseEvidenceItem)
        .join(
            BidEnterpriseEvidencePackageItem,
            BidEnterpriseEvidencePackageItem.package_id
            == BidEnterpriseEvidencePackage.id,
        )
        .join(
            BidEnterpriseEvidenceItem,
            BidEnterpriseEvidenceItem.id
            == BidEnterpriseEvidencePackageItem.evidence_item_id,
        )
        .filter(
            BidEnterpriseEvidencePackage.id == package_id,
            BidEnterpriseEvidencePackage.status == "frozen",
            BidEnterpriseEvidencePackageItem.slot_code == slot_code,
            BidEnterpriseEvidenceItem.id == evidence_item_id,
            BidEnterpriseEvidenceItem.status == "frozen",
        )
        .one_or_none()
    )
    if pair is None:
        raise BidEnterpriseEvidenceImportError(
            "BID_ENTERPRISE_EVIDENCE_ITEM_NOT_IN_PACKAGE_SLOT"
        )
    return pair
