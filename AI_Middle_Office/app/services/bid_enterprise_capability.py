"""Phase 4C immutable enterprise capability baselines and governed facts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment_config import (
    BidEnterpriseSnapshot,
    BidEnterpriseSnapshotRecord,
)
from app.models.bid_assessment_results import (
    BidFactAssertion,
    BidFactEnterpriseLink,
)
from app.services.bid_assessment_eventing import (
    append_audit_log,
    as_utc,
    canonical_hash,
    canonical_json,
)
from app.services.bid_task_runtime import TaskLeaseClaim, lock_task_claim
from app.services.bid_upload_file_storage import (
    BidUploadObjectStorage,
    get_bid_upload_object_storage,
)


ENTERPRISE_CAPABILITY_SCHEMA = "bid.enterprise.capability-snapshot.v1"
ENTERPRISE_RECORD_SCHEMA = "bid.enterprise.capability-record.v1"
ENTERPRISE_CATALOG_VERSION = "bid-enterprise-capability-catalog-v1"
ENTERPRISE_OBJECT_PREFIX = "bid-enterprise-capability/v1"
ENTERPRISE_SNAPSHOT_AUTHORITY = "bid-enterprise-snapshot-authority-v1"
ENTERPRISE_FACT_AUTHORITY = "bid-enterprise-fact-authority-v1"
ENTERPRISE_BASELINE_VALIDATION_SCHEMA = "bid.enterprise.baseline-validation.v1"
ENTERPRISE_SLOT_CODES = tuple(f"I{index:02d}" for index in range(1, 12))

ENTERPRISE_SLOT_SPECS: dict[str, tuple[str, str]] = {
    "I01": ("enterprise.identity.legal_name", "enterprise_identity"),
    "I02": ("enterprise.qualifications.active_records", "enterprise_record_list"),
    "I03": ("enterprise.safety_license.active_record", "enterprise_record"),
    "I04": ("enterprise.performance.records", "enterprise_record_list"),
    "I05": ("enterprise.personnel.available_records", "enterprise_record_list"),
    "I06": ("enterprise.financial.capacity", "enterprise_capacity"),
    "I07": ("enterprise.guarantee.capacity", "enterprise_capacity"),
    "I08": ("enterprise.bid_preparation.capacity", "enterprise_capacity"),
    "I09": ("enterprise.prohibited_risk.rules", "enterprise_rule_list"),
    "I10": ("enterprise.compliance.current_records", "enterprise_record"),
    "I11": ("enterprise.client_risk.current_records", "enterprise_record_list"),
}

ENTERPRISE_SLOT_LABELS: dict[str, str] = {
    "I01": "企业法定主体",
    "I02": "有效资质",
    "I03": "安全生产许可证",
    "I04": "相似项目业绩",
    "I05": "可用人员与证书",
    "I06": "资金能力",
    "I07": "保证金与保函能力",
    "I08": "投标准备能力",
    "I09": "企业禁投规则",
    "I10": "当前合规状态",
    "I11": "客户风险记录",
}

ENTERPRISE_GATE_SLOTS: dict[str, tuple[str, ...]] = {
    "HG01": (),
    "HG02": ("I02", "I03"),
    "HG03": ("I04", "I05"),
    "HG04": ("I10",),
    "HG05": ("I06", "I07"),
    "HG06": ("I08",),
    "HG07": ("I09", "I11"),
}


class BidEnterpriseCapabilityError(RuntimeError):
    code = "BID_ENTERPRISE_CAPABILITY_ERROR"


def _database_utc_now(db: Session) -> datetime:
    """Use the transaction clock without coupling snapshot authority to Run bootstrap."""

    if db.get_bind().dialect.name == "mysql":
        value = db.execute(select(func.utc_timestamp(6))).scalar_one()
    else:
        value = db.execute(select(func.current_timestamp())).scalar_one()
    if not isinstance(value, datetime):
        raise BidEnterpriseCapabilityError("BID_DATABASE_TIME_INVALID")
    return as_utc(value)


@dataclass(frozen=True)
class FrozenEnterpriseSnapshotResult:
    snapshot: BidEnterpriseSnapshot
    created: bool
    projection: dict[str, Any]


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _record_object_key(payload_hash: str) -> str:
    return f"{ENTERPRISE_OBJECT_PREFIX}/{payload_hash[:2]}/{payload_hash}.json"


def _validate_json_size(value: Any, *, limit: int = 64 * 1024) -> bytes:
    encoded = canonical_json(value).encode("utf-8")
    if not encoded or len(encoded) > limit:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_RECORD_SIZE_INVALID")
    return encoded


def _validate_slot_value(slot_code: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_SHAPE_INVALID")
    list_keys = {
        "I02": "records",
        "I04": "projects",
        "I05": "people",
        "I09": "rules",
        "I11": "records",
    }
    list_key = list_keys.get(slot_code)
    if list_key is not None:
        items = value.get(list_key)
        if not isinstance(items, list) or len(items) > 500 or not all(
            isinstance(item, dict) for item in items
        ):
            raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_SHAPE_INVALID")
    if slot_code == "I01" and not str(value.get("legal_name") or "").strip():
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_SHAPE_INVALID")
    if slot_code == "I03" and (
        not str(value.get("license_no") or "").strip()
        or not str(value.get("status") or "").strip()
    ):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_SHAPE_INVALID")
    if slot_code == "I06" and "available_cash_cny" not in value:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_SHAPE_INVALID")
    if slot_code == "I07" and not (
        "max_bond_cny" in value or "available_cash_cny" in value
    ):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_SHAPE_INVALID")
    if slot_code == "I08" and "available_person_days" not in value:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_SHAPE_INVALID")
    if slot_code == "I10" and not str(value.get("status") or "").strip():
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_SHAPE_INVALID")


def _normalize_command_record(record: dict[str, Any], *, as_of: datetime) -> dict[str, Any]:
    slot_code = str(record.get("slot_code") or "")
    spec = ENTERPRISE_SLOT_SPECS.get(slot_code)
    if spec is None:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SLOT_INVALID")
    coverage_status = str(record.get("coverage_status") or "")
    value = record.get("value")
    if coverage_status not in {"supported", "partial", "unknown"}:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_COVERAGE_INVALID")
    if coverage_status == "unknown" and value is not None:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_UNKNOWN_VALUE_FORBIDDEN")
    if coverage_status != "unknown" and value is None:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALUE_REQUIRED")
    if coverage_status != "unknown":
        _validate_slot_value(slot_code, value)
    valid_from = record.get("valid_from")
    valid_to = record.get("valid_to")
    if valid_from is not None:
        valid_from = as_utc(valid_from)
    if valid_to is not None:
        valid_to = as_utc(valid_to)
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_VALIDITY_INVALID")
    source_status = str(record.get("source_status") or "")
    if source_status not in {"verified", "self_reported", "imported", "unknown"}:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SOURCE_STATUS_INVALID")
    if coverage_status == "supported" and source_status == "unknown":
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SUPPORTED_SOURCE_UNKNOWN")
    payload = {
        "schema": ENTERPRISE_RECORD_SCHEMA,
        "slot_code": slot_code,
        "slot_key": spec[0],
        "value_type": spec[1],
        "coverage_status": coverage_status,
        "value": value,
        "provenance": {
            "source_record_id": str(record.get("source_record_id") or ""),
            "source_version": str(record.get("source_version") or ""),
            "source_status": source_status,
            "source_label": str(record.get("source_label") or "").strip(),
            "as_of": _utc_text(as_of),
            "valid_from": _utc_text(valid_from),
            "valid_to": _utc_text(valid_to),
            "checked_at": _utc_text(
                as_utc(record["checked_at"]) if record.get("checked_at") is not None else None
            ),
        },
    }
    if not payload["provenance"]["source_record_id"] or not payload["provenance"]["source_version"]:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SOURCE_IDENTITY_INVALID")
    if not payload["provenance"]["source_label"]:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SOURCE_LABEL_INVALID")
    _validate_json_size(payload)
    return payload


def _prepare_snapshot_candidate(
    command: dict[str, Any],
    *,
    current_time: datetime,
) -> tuple[datetime, list[dict[str, Any]], list[dict[str, Any]], str]:
    """Normalize and hash a candidate without writing objects or database rows."""

    as_of = as_utc(command["as_of"])
    if as_of > as_utc(current_time) + timedelta(minutes=5):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SNAPSHOT_AS_OF_FUTURE")
    raw_records = list(command.get("records") or [])
    if len(raw_records) != len(ENTERPRISE_SLOT_CODES):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SNAPSHOT_RECORDS_INCOMPLETE")
    normalized_payloads = [
        _normalize_command_record(dict(record), as_of=as_of)
        for record in raw_records
    ]
    if sorted(str(payload["slot_code"]) for payload in normalized_payloads) != list(
        ENTERPRISE_SLOT_CODES
    ):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SNAPSHOT_RECORDS_INCOMPLETE")

    record_metadata: list[dict[str, Any]] = []
    for payload in normalized_payloads:
        payload_hash = canonical_hash(payload)
        provenance = dict(payload["provenance"])
        record_metadata.append(
            {
                "record_type": str(payload["slot_code"]),
                "source_record_id": str(provenance["source_record_id"]),
                "source_version": str(provenance["source_version"]),
                "source_status": str(provenance["source_status"]),
                "valid_from": provenance.get("valid_from"),
                "valid_to": provenance.get("valid_to"),
                "payload_hash": payload_hash,
                "object_ref": _record_object_key(payload_hash),
            }
        )
    snapshot_hash = canonical_hash(
        _snapshot_hash_payload(as_of=as_of, records=record_metadata)
    )
    return as_of, normalized_payloads, record_metadata, snapshot_hash


def _snapshot_hash_payload(
    *,
    as_of: datetime,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": ENTERPRISE_CAPABILITY_SCHEMA,
        "source_catalog_version": ENTERPRISE_CATALOG_VERSION,
        "as_of": _utc_text(as_of),
        "records": sorted(records, key=lambda item: str(item["record_type"])),
    }


def compute_snapshot_hash_from_rows(
    snapshot: BidEnterpriseSnapshot,
    rows: list[BidEnterpriseSnapshotRecord],
) -> str:
    records = [
        {
            "record_type": str(row.record_type),
            "source_record_id": str(row.source_record_id),
            "source_version": str(row.source_version),
            "source_status": str(row.source_status),
            "valid_from": _utc_text(row.valid_from),
            "valid_to": _utc_text(row.valid_to),
            "payload_hash": str(row.payload_hash),
            "object_ref": str(row.object_ref),
        }
        for row in rows
    ]
    return canonical_hash(
        _snapshot_hash_payload(as_of=as_utc(snapshot.as_of), records=records)
    )


def validate_frozen_snapshot_metadata(
    db: Session,
    snapshot: BidEnterpriseSnapshot,
) -> list[BidEnterpriseSnapshotRecord]:
    if (
        str(snapshot.status) != "frozen"
        or not snapshot.snapshot_hash
        or str(snapshot.source_catalog_version) != ENTERPRISE_CATALOG_VERSION
    ):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SNAPSHOT_NOT_GOVERNED")
    rows = (
        db.query(BidEnterpriseSnapshotRecord)
        .filter(BidEnterpriseSnapshotRecord.snapshot_id == snapshot.id)
        .order_by(BidEnterpriseSnapshotRecord.record_type.asc())
        .all()
    )
    if [str(row.record_type) for row in rows] != list(ENTERPRISE_SLOT_CODES):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SNAPSHOT_RECORDS_INCOMPLETE")
    if compute_snapshot_hash_from_rows(snapshot, rows) != str(snapshot.snapshot_hash):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SNAPSHOT_HASH_MISMATCH")
    return rows


def _read_record_payload(
    storage: BidUploadObjectStorage,
    row: BidEnterpriseSnapshotRecord,
) -> dict[str, Any]:
    object_ref = str(row.object_ref or "")
    if not object_ref.startswith(ENTERPRISE_OBJECT_PREFIX + "/"):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_OBJECT_REF_INVALID")
    stream = storage.open_read(object_key=object_ref)
    try:
        content = stream.read(64 * 1024 + 1)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    if len(content) > 64 * 1024:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_RECORD_SIZE_INVALID")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_RECORD_INVALID") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != ENTERPRISE_RECORD_SCHEMA
        or payload.get("slot_code") != str(row.record_type)
        or canonical_hash(payload) != str(row.payload_hash)
    ):
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_RECORD_HASH_MISMATCH")
    return payload


def freeze_enterprise_snapshot(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
    request_id: str,
    storage: BidUploadObjectStorage | None = None,
    now: datetime | None = None,
    expected_snapshot_hash: str | None = None,
) -> FrozenEnterpriseSnapshotResult:
    if not settings.feature_bid_assessment_phase4_enterprise_capability:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_CAPABILITY_DISABLED")
    current_time = as_utc(now) if now is not None else _database_utc_now(db)
    as_of, normalized_payloads, record_metadata, snapshot_hash = (
        _prepare_snapshot_candidate(command, current_time=current_time)
    )
    if expected_snapshot_hash is not None and expected_snapshot_hash != snapshot_hash:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_CANDIDATE_HASH_MISMATCH")

    encoded_by_hash: dict[str, bytes] = {}
    for payload in normalized_payloads:
        encoded = _validate_json_size(payload)
        payload_hash = canonical_hash(payload)
        encoded_by_hash[payload_hash] = encoded
    existing = (
        db.query(BidEnterpriseSnapshot)
        .filter(BidEnterpriseSnapshot.snapshot_hash == snapshot_hash)
        .one_or_none()
    )
    if existing is not None:
        rows = validate_frozen_snapshot_metadata(db, existing)
        return FrozenEnterpriseSnapshotResult(
            snapshot=existing,
            created=False,
            projection=project_enterprise_snapshot(existing, rows, include_values=False),
        )

    object_storage = storage or get_bid_upload_object_storage()
    for metadata in record_metadata:
        encoded = encoded_by_hash[str(metadata["payload_hash"])]
        object_storage.put(
            stream=BytesIO(encoded),
            object_key=str(metadata["object_ref"]),
            size_bytes=len(encoded),
            mime_type="application/json",
        )

    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bid-enterprise:{snapshot_hash}"))
    version = f"enterprise-{as_of:%Y%m%d%H%M%S}-{snapshot_hash[:12]}"
    snapshot = BidEnterpriseSnapshot(
        id=snapshot_id,
        version=version,
        as_of=as_of,
        snapshot_hash=snapshot_hash,
        source_catalog_version=ENTERPRISE_CATALOG_VERSION,
        status="frozen",
        error_code=None,
        created_by=int(actor_id),
        frozen_by=int(actor_id),
        frozen_at=current_time,
        row_version=1,
        created_at=current_time,
        updated_at=current_time,
    )
    db.add(snapshot)
    rows: list[BidEnterpriseSnapshotRecord] = []
    for metadata in record_metadata:
        row_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{snapshot_id}:{metadata['record_type']}:{metadata['payload_hash']}",
            )
        )
        row = BidEnterpriseSnapshotRecord(
            id=row_id,
            snapshot_id=snapshot_id,
            record_type=str(metadata["record_type"]),
            source_record_id=str(metadata["source_record_id"]),
            source_version=str(metadata["source_version"]),
            valid_from=(
                datetime.fromisoformat(str(metadata["valid_from"]).replace("Z", "+00:00"))
                if metadata.get("valid_from")
                else None
            ),
            valid_to=(
                datetime.fromisoformat(str(metadata["valid_to"]).replace("Z", "+00:00"))
                if metadata.get("valid_to")
                else None
            ),
            source_status=str(metadata["source_status"]),
            payload_hash=str(metadata["payload_hash"]),
            object_ref=str(metadata["object_ref"]),
            created_at=current_time,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=f"user:{actor_id}",
        action="bid.enterprise_snapshot.freeze",
        entity_type="enterprise_snapshot",
        entity_id=snapshot_id,
        outcome="succeeded",
        request_id=request_id,
        after={"version": version, "snapshot_hash": snapshot_hash},
        metadata={
            "authority_version": ENTERPRISE_SNAPSHOT_AUTHORITY,
            "change_note": str(command.get("change_note") or "")[:1000],
            "record_count": len(rows),
        },
        occurred_at=current_time,
    )
    projection = project_enterprise_snapshot(snapshot, rows, include_values=False)
    return FrozenEnterpriseSnapshotResult(snapshot=snapshot, created=True, projection=projection)


def project_enterprise_snapshot(
    snapshot: BidEnterpriseSnapshot,
    rows: list[BidEnterpriseSnapshotRecord],
    *,
    include_values: bool,
    storage: BidUploadObjectStorage | None = None,
) -> dict[str, Any]:
    object_storage = storage or (get_bid_upload_object_storage() if include_values else None)
    records: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "record_id": str(row.id),
            "slot_code": str(row.record_type),
            "slot_key": ENTERPRISE_SLOT_SPECS.get(str(row.record_type), ("unknown", "unknown"))[0],
            "source_record_id": str(row.source_record_id),
            "source_version": str(row.source_version),
            "source_status": str(row.source_status),
            "valid_from": _utc_text(row.valid_from),
            "valid_to": _utc_text(row.valid_to),
            "payload_hash": str(row.payload_hash),
        }
        if include_values and object_storage is not None:
            payload = _read_record_payload(object_storage, row)
            item.update(
                {
                    "coverage_status": str(payload["coverage_status"]),
                    "value_type": str(payload["value_type"]),
                    "value": payload.get("value"),
                    "source_label": str(payload["provenance"]["source_label"]),
                    "checked_at": payload["provenance"].get("checked_at"),
                }
            )
        records.append(item)
    return {
        "schema": ENTERPRISE_CAPABILITY_SCHEMA,
        "snapshot_id": str(snapshot.id),
        "version": str(snapshot.version),
        "status": str(snapshot.status),
        "as_of": _utc_text(snapshot.as_of),
        "frozen_at": _utc_text(snapshot.frozen_at),
        "source_catalog_version": str(snapshot.source_catalog_version),
        "snapshot_hash": str(snapshot.snapshot_hash or ""),
        "record_count": len(rows),
        "complete": [str(row.record_type) for row in rows] == list(ENTERPRISE_SLOT_CODES),
        "records": records,
    }


def latest_frozen_enterprise_snapshot(
    db: Session,
    *,
    include_values: bool,
    storage: BidUploadObjectStorage | None = None,
) -> dict[str, Any] | None:
    snapshot = (
        db.query(BidEnterpriseSnapshot)
        .filter(BidEnterpriseSnapshot.status == "frozen")
        .order_by(
            BidEnterpriseSnapshot.as_of.desc(),
            BidEnterpriseSnapshot.frozen_at.desc(),
            BidEnterpriseSnapshot.id.desc(),
        )
        .first()
    )
    if snapshot is None:
        return None
    rows = validate_frozen_snapshot_metadata(db, snapshot)
    return project_enterprise_snapshot(
        snapshot,
        rows,
        include_values=include_values,
        storage=storage,
    )


def _baseline_diff_hash(payload: dict[str, Any]) -> str:
    """Compare governed business content without version observation timestamps."""

    comparable = json.loads(canonical_json(payload))
    provenance = dict(comparable.get("provenance") or {})
    provenance.pop("as_of", None)
    provenance.pop("checked_at", None)
    comparable["provenance"] = provenance
    return canonical_hash(comparable)


def _effective_coverage(
    payload: dict[str, Any],
    *,
    as_of: datetime,
) -> tuple[str, list[str]]:
    provenance = dict(payload.get("provenance") or {})
    valid_from = provenance.get("valid_from")
    valid_to = provenance.get("valid_to")
    if valid_from and as_utc(datetime.fromisoformat(str(valid_from).replace("Z", "+00:00"))) > as_of:
        return "not_yet_valid", ["ENTERPRISE_SLOT_NOT_YET_VALID"]
    if valid_to and as_utc(datetime.fromisoformat(str(valid_to).replace("Z", "+00:00"))) < as_of:
        return "expired", ["ENTERPRISE_SLOT_EXPIRED"]
    coverage_status = str(payload.get("coverage_status") or "unknown")
    reasons = {
        "supported": ["ENTERPRISE_SLOT_READY"],
        "partial": ["ENTERPRISE_SLOT_PARTIAL"],
        "unknown": ["ENTERPRISE_SLOT_UNKNOWN"],
    }[coverage_status]
    source_status = str(provenance.get("source_status") or "unknown")
    if coverage_status != "unknown" and source_status == "self_reported":
        reasons.append("ENTERPRISE_SOURCE_SELF_REPORTED")
    return coverage_status, reasons


def preview_enterprise_baseline(
    db: Session,
    *,
    command: dict[str, Any],
    storage: BidUploadObjectStorage | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and diff a baseline without writing objects, rows, audit, or outbox."""

    if not settings.feature_bid_assessment_phase4_enterprise_capability:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_CAPABILITY_DISABLED")
    current_time = as_utc(now) if now is not None else _database_utc_now(db)
    as_of, payloads, metadata_rows, snapshot_hash = _prepare_snapshot_candidate(
        command,
        current_time=current_time,
    )
    current_snapshot = (
        db.query(BidEnterpriseSnapshot)
        .filter(BidEnterpriseSnapshot.status == "frozen")
        .order_by(
            BidEnterpriseSnapshot.as_of.desc(),
            BidEnterpriseSnapshot.frozen_at.desc(),
            BidEnterpriseSnapshot.id.desc(),
        )
        .first()
    )
    current_rows: list[BidEnterpriseSnapshotRecord] = []
    if current_snapshot is not None:
        current_rows = validate_frozen_snapshot_metadata(db, current_snapshot)
    object_storage = storage or (
        get_bid_upload_object_storage() if current_rows else None
    )
    current_by_slot = {
        str(row.record_type): {
            "source_record_id": str(row.source_record_id),
            "source_version": str(row.source_version),
            "source_status": str(row.source_status),
            "valid_from": _utc_text(row.valid_from),
            "valid_to": _utc_text(row.valid_to),
            "payload_hash": str(row.payload_hash),
            "object_ref": str(row.object_ref),
            "baseline_diff_hash": _baseline_diff_hash(
                _read_record_payload(object_storage, row)
            ),
        }
        for row in current_rows
    }
    metadata_by_slot = {
        str(item["record_type"]): item for item in metadata_rows
    }
    payload_by_slot = {str(item["slot_code"]): item for item in payloads}

    slot_results: list[dict[str, Any]] = []
    effective_by_slot: dict[str, str] = {}
    acceptance_ready_by_slot: dict[str, bool] = {}
    coverage_counts = {
        "supported": 0,
        "partial": 0,
        "unknown": 0,
        "not_yet_valid": 0,
        "expired": 0,
    }
    changed_slot_count = 0
    for slot_code in ENTERPRISE_SLOT_CODES:
        payload = payload_by_slot[slot_code]
        metadata = metadata_by_slot[slot_code]
        effective_status, reason_codes = _effective_coverage(payload, as_of=as_of)
        effective_by_slot[slot_code] = effective_status
        source_status = str(payload["provenance"]["source_status"])
        acceptance_ready_by_slot[slot_code] = bool(
            effective_status == "supported"
            and source_status in {"verified", "imported"}
        )
        coverage_counts[effective_status] += 1
        previous = current_by_slot.get(slot_code)
        change_type = (
            "added"
            if previous is None
            else (
                "unchanged"
                if previous["baseline_diff_hash"] == _baseline_diff_hash(payload)
                else "changed"
            )
        )
        if change_type != "unchanged":
            changed_slot_count += 1
        slot_results.append(
            {
                "slot_code": slot_code,
                "slot_key": ENTERPRISE_SLOT_SPECS[slot_code][0],
                "label": ENTERPRISE_SLOT_LABELS[slot_code],
                "coverage_status": str(payload["coverage_status"]),
                "effective_status": effective_status,
                "validation_status": (
                    "ready"
                    if acceptance_ready_by_slot[slot_code]
                    else "review_required"
                ),
                "source_status": source_status,
                "change_type": change_type,
                "candidate_payload_hash": str(metadata["payload_hash"]),
                "previous_payload_hash": (
                    str(previous["payload_hash"]) if previous is not None else None
                ),
                "reason_codes": reason_codes,
            }
        )

    gate_results: list[dict[str, Any]] = []
    for gate_code, slot_codes in ENTERPRISE_GATE_SLOTS.items():
        if not slot_codes:
            gate_results.append(
                {
                    "gate_code": gate_code,
                    "status": "deferred_tender",
                    "enterprise_slot_codes": [],
                    "unresolved_slot_codes": [],
                    "reason_codes": ["TENDER_FACT_REQUIRED_AT_RUN_TIME"],
                }
            )
            continue
        unresolved = [
            slot_code
            for slot_code in slot_codes
            if not acceptance_ready_by_slot[slot_code]
        ]
        gate_results.append(
            {
                "gate_code": gate_code,
                "status": "ready" if not unresolved else "review_required",
                "enterprise_slot_codes": list(slot_codes),
                "unresolved_slot_codes": unresolved,
                "reason_codes": (
                    ["ENTERPRISE_GATE_INPUTS_READY"]
                    if not unresolved
                    else ["ENTERPRISE_GATE_INPUTS_UNRESOLVED"]
                ),
            }
        )

    return {
        "schema": ENTERPRISE_BASELINE_VALIDATION_SCHEMA,
        "source_catalog_version": ENTERPRISE_CATALOG_VERSION,
        "as_of": _utc_text(as_of),
        "candidate_snapshot_hash": snapshot_hash,
        "base_snapshot": (
            {
                "snapshot_id": str(current_snapshot.id),
                "version": str(current_snapshot.version),
                "snapshot_hash": str(current_snapshot.snapshot_hash),
            }
            if current_snapshot is not None
            else None
        ),
        "no_change": bool(
            current_snapshot is not None and changed_slot_count == 0
        ),
        "changed_slot_count": changed_slot_count,
        "coverage_counts": coverage_counts,
        "can_freeze": True,
        "acceptance_ready": bool(
            acceptance_ready_by_slot.get("I01") is True
            and all(item["status"] != "review_required" for item in gate_results)
        ),
        "slots": slot_results,
        "hard_gate_readiness": gate_results,
    }


def materialize_enterprise_snapshot_facts(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    storage: BidUploadObjectStorage | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not settings.feature_bid_assessment_phase4_enterprise_capability:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_CAPABILITY_DISABLED")
    current_time = as_utc(now) if now is not None else _database_utc_now(db)
    attempt, task, run = lock_task_claim(db, claim, now=current_time)
    if str(task.task_type) != "build_enterprise_snapshot":
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_TASK_INVALID")
    if getattr(settings, "feature_bid_assessment_phase4_fact_verification", False):
        from app.services.bid_hard_gate_fact_verification import (
            materialize_hard_gate_comparison_facts,
        )

        return materialize_hard_gate_comparison_facts(
            db,
            run=run,
            task_id=str(task.id),
            attempt_id=str(attempt.id),
            current_time=current_time,
        )
    snapshot = (
        db.query(BidEnterpriseSnapshot)
        .filter(BidEnterpriseSnapshot.id == run.enterprise_snapshot_id)
        .one_or_none()
    )
    if snapshot is None:
        raise BidEnterpriseCapabilityError("BID_ENTERPRISE_SNAPSHOT_NOT_FOUND")
    rows = validate_frozen_snapshot_metadata(db, snapshot)
    object_storage = storage or get_bid_upload_object_storage()
    assertion_ids: list[str] = []
    unknown_slots: list[str] = []
    for row in rows:
        payload = _read_record_payload(object_storage, row)
        coverage_status = str(payload["coverage_status"])
        slot_code = str(payload["slot_code"])
        fact_slot, value_type = ENTERPRISE_SLOT_SPECS[slot_code]
        evaluation_time = as_utc(run.evaluation_time)
        outside_validity = bool(
            (row.valid_from is not None and as_utc(row.valid_from) > evaluation_time)
            or (row.valid_to is not None and as_utc(row.valid_to) < evaluation_time)
        )
        if coverage_status == "unknown" or outside_validity:
            unknown_slots.append(fact_slot)
            continue
        value = payload.get("value")
        assertion_payload = {
            "authority_version": ENTERPRISE_FACT_AUTHORITY,
            "run_id": str(run.id),
            "task_id": str(task.id),
            "attempt_id": str(attempt.id),
            "enterprise_snapshot_id": str(snapshot.id),
            "snapshot_record_id": str(row.id),
            "payload_hash": str(row.payload_hash),
            "fact_slot": fact_slot,
            "scope_type": "assessment",
            "scope_id": str(run.assessment_id),
            "value_type": value_type,
            "value_hash": canonical_hash(value),
            "coverage_status": coverage_status,
            "asserted_at": _utc_text(run.evaluation_time),
        }
        assertion_hash = canonical_hash(assertion_payload)
        assertion = (
            db.query(BidFactAssertion)
            .filter(
                BidFactAssertion.run_id == run.id,
                BidFactAssertion.assertion_hash == assertion_hash,
            )
            .one_or_none()
        )
        reason_codes = [
            "ENTERPRISE_SNAPSHOT_FROZEN",
            f"ENTERPRISE_SLOT_{slot_code}",
            f"ENTERPRISE_SOURCE_{str(row.source_status).upper()}",
        ]
        if coverage_status == "partial":
            reason_codes.append("ENTERPRISE_RECORD_PARTIAL")
        if assertion is None:
            assertion = BidFactAssertion(
                id=str(uuid.uuid4()),
                assessment_id=str(run.assessment_id),
                run_id=str(run.id),
                task_id=str(task.id),
                source_task_attempt_id=str(attempt.id),
                model_result_id=None,
                fact_catalog_version_id=str(run.fact_catalog_version_id),
                fact_slot=fact_slot,
                scope_type="assessment",
                scope_id=str(run.assessment_id),
                value_type=value_type,
                value_json=value,
                value_hash=canonical_hash(value),
                source_type="enterprise",
                confidence="high" if coverage_status == "supported" else "medium",
                status="accepted",
                asserted_at=as_utc(run.evaluation_time),
                assertion_hash=assertion_hash,
                reason_codes_json=reason_codes,
                created_at=current_time,
            )
            db.add(assertion)
            db.flush()
            link_payload = {
                "assertion_id": str(assertion.id),
                "snapshot_record_id": str(row.id),
                "snapshot_id": str(snapshot.id),
                "record_type": slot_code,
                "source_record_id": str(row.source_record_id),
                "source_version": str(row.source_version),
                "payload_hash": str(row.payload_hash),
            }
            db.add(
                BidFactEnterpriseLink(
                    assertion_id=str(assertion.id),
                    snapshot_record_id=str(row.id),
                    record_type=slot_code,
                    source_record_id=str(row.source_record_id),
                    source_version=str(row.source_version),
                    payload_hash=str(row.payload_hash),
                    link_hash=canonical_hash(link_payload),
                    created_at=current_time,
                )
            )
        assertion_ids.append(str(assertion.id))
    db.flush()
    return {
        "schema": "bid.enterprise.fact-materialization.v1",
        "authority_version": ENTERPRISE_FACT_AUTHORITY,
        "run_id": str(run.id),
        "task_id": str(task.id),
        "enterprise_snapshot_id": str(snapshot.id),
        "enterprise_snapshot_hash": str(snapshot.snapshot_hash),
        "assertion_ids": sorted(assertion_ids),
        "unknown_fact_slots": sorted(unknown_slots),
        "materialized_count": len(assertion_ids),
    }
