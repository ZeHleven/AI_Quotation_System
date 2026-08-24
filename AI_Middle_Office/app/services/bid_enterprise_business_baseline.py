"""Phase 4D-1 immutable business verification for enterprise snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment_config import BidEnterpriseSnapshot
from app.models.bid_assessment_release import (
    BidEnterpriseBusinessBaseline,
    BidEnterpriseEvidencePackage,
)
from app.services.bid_assessment_eventing import append_audit_log, as_utc, canonical_hash
from app.services.bid_enterprise_capability import (
    ENTERPRISE_SLOT_CODES,
    BidEnterpriseCapabilityError,
    project_enterprise_snapshot,
    validate_frozen_snapshot_metadata,
)
from app.services.bid_enterprise_evidence_import import (
    BidEnterpriseEvidenceImportError,
    validate_enterprise_evidence_package_at,
    validate_package_item_reference,
)


BUSINESS_BASELINE_SCHEMA = "bid.enterprise.business-baseline.v1"
BUSINESS_BASELINE_VALIDATION_SCHEMA = "bid.enterprise.business-baseline-validation.v1"
BUSINESS_BASELINE_AUTHORITY = "bid-enterprise-business-baseline-authority-v1"
BUSINESS_BASELINE_EVIDENCE_CLASSES = frozenset(
    {
        "official_document",
        "internal_system",
        "audited_record",
        "management_attestation",
        "not_available",
    }
)
_HASHED_EVIDENCE_CLASSES = frozenset(
    {"official_document", "internal_system", "audited_record"}
)
_BASELINE_NAMESPACE = uuid.UUID("872c9f56-f9b7-42ac-a112-c11c98521fbf")


class BidEnterpriseBusinessBaselineError(RuntimeError):
    code = "BID_ENTERPRISE_BUSINESS_BASELINE_ERROR"


@dataclass(frozen=True)
class FrozenEnterpriseBusinessBaselineResult:
    baseline: BidEnterpriseBusinessBaseline
    created: bool
    projection: dict[str, Any]


def _database_utc_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "mysql":
        value = db.execute(select(func.utc_timestamp(6))).scalar_one()
    else:
        value = db.execute(select(func.current_timestamp())).scalar_one()
    if not isinstance(value, datetime):
        raise BidEnterpriseBusinessBaselineError("BID_DATABASE_TIME_INVALID")
    return as_utc(value)


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _latest_frozen_snapshot(db: Session, *, lock: bool) -> BidEnterpriseSnapshot | None:
    query = (
        db.query(BidEnterpriseSnapshot)
        .filter(BidEnterpriseSnapshot.status == "frozen")
        .order_by(
            BidEnterpriseSnapshot.as_of.desc(),
            BidEnterpriseSnapshot.frozen_at.desc(),
            BidEnterpriseSnapshot.id.desc(),
        )
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def _normalize_reference(value: Any) -> str | None:
    normalized = str(value or "").strip() or None
    if normalized is None:
        return None
    if (
        len(normalized) > 300
        or "://" in normalized
        or normalized.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:[\\/]", normalized)
    ):
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_EVIDENCE_REF_INVALID"
        )
    return normalized


def _normalize_reviews(command: dict[str, Any]) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for raw in command.get("slot_reviews") or []:
        item = dict(raw)
        slot_code = str(item.get("slot_code") or "")
        disposition = str(item.get("disposition") or "")
        evidence_class = str(item.get("evidence_class") or "")
        evidence_hash = str(item.get("evidence_hash") or "").strip().lower() or None
        note = str(item.get("note") or "").strip() or None
        if slot_code not in ENTERPRISE_SLOT_CODES:
            raise BidEnterpriseBusinessBaselineError(
                "BID_ENTERPRISE_BUSINESS_SLOT_INVALID"
            )
        if disposition not in {"confirmed", "correction_required", "not_reviewed"}:
            raise BidEnterpriseBusinessBaselineError(
                "BID_ENTERPRISE_BUSINESS_DISPOSITION_INVALID"
            )
        if evidence_class not in BUSINESS_BASELINE_EVIDENCE_CLASSES:
            raise BidEnterpriseBusinessBaselineError(
                "BID_ENTERPRISE_BUSINESS_EVIDENCE_CLASS_INVALID"
            )
        if evidence_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", evidence_hash):
            raise BidEnterpriseBusinessBaselineError(
                "BID_ENTERPRISE_BUSINESS_EVIDENCE_HASH_INVALID"
            )
        normalized_review = {
            "slot_code": slot_code,
            "disposition": disposition,
            "evidence_class": evidence_class,
            "evidence_ref": _normalize_reference(item.get("evidence_ref")),
            "evidence_hash": evidence_hash,
            "note": note,
        }
        evidence_item_id = str(item.get("evidence_item_id") or "").strip() or None
        if evidence_item_id is not None:
            normalized_review["evidence_item_id"] = evidence_item_id
        reviews.append(normalized_review)
    reviews.sort(key=lambda item: item["slot_code"])
    if [item["slot_code"] for item in reviews] != list(ENTERPRISE_SLOT_CODES):
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_REVIEW_SET_INVALID"
        )
    return reviews


def _effective_record_status(record: dict[str, Any], *, as_of: datetime) -> str:
    coverage = str(record.get("coverage_status") or "unknown")
    valid_from = record.get("valid_from")
    valid_to = record.get("valid_to")
    if valid_from and as_utc(datetime.fromisoformat(str(valid_from).replace("Z", "+00:00"))) > as_of:
        return "not_yet_valid"
    if valid_to and as_utc(datetime.fromisoformat(str(valid_to).replace("Z", "+00:00"))) < as_of:
        return "expired"
    return coverage


def _projection(row: BidEnterpriseBusinessBaseline) -> dict[str, Any]:
    return {
        "schema": BUSINESS_BASELINE_SCHEMA,
        "business_baseline_id": str(row.id),
        "version": str(row.version),
        "snapshot_id": str(row.snapshot_id),
        **(
            {
                "evidence_package_id": str(row.evidence_package_id),
                "evidence_package_hash": str(row.evidence_package_hash),
            }
            if row.evidence_package_id and row.evidence_package_hash
            else {}
        ),
        "status": str(row.status),
        "verification_outcome": str(row.verification_outcome),
        "reviewer_id": int(row.reviewer_id),
        "review_note": str(row.review_note),
        "slot_reviews": list(row.slot_reviews_json or []),
        "source_hashes": dict(row.source_hashes_json or {}),
        "candidate_hash": str(row.candidate_hash),
        "baseline_hash": str(row.baseline_hash),
        "reviewed_at": _utc_text(row.reviewed_at),
    }


def get_enterprise_business_baseline(
    db: Session,
    *,
    snapshot_id: str | None = None,
) -> dict[str, Any] | None:
    query = db.query(BidEnterpriseBusinessBaseline)
    if snapshot_id:
        query = query.filter(BidEnterpriseBusinessBaseline.snapshot_id == snapshot_id)
    row = query.order_by(
        BidEnterpriseBusinessBaseline.reviewed_at.desc(),
        BidEnterpriseBusinessBaseline.id.desc(),
    ).first()
    return None if row is None else _projection(row)


def latest_business_snapshot(
    db: Session,
    *,
    effective_at: datetime | None = None,
    lock: bool = False,
) -> tuple[BidEnterpriseSnapshot, BidEnterpriseBusinessBaseline] | None:
    query = (
        db.query(BidEnterpriseSnapshot, BidEnterpriseBusinessBaseline)
        .join(
            BidEnterpriseBusinessBaseline,
            BidEnterpriseBusinessBaseline.snapshot_id == BidEnterpriseSnapshot.id,
        )
        .filter(
            BidEnterpriseSnapshot.status == "frozen",
            BidEnterpriseBusinessBaseline.status == "frozen",
        )
    )
    if effective_at is not None:
        current_time = as_utc(effective_at)
        query = query.filter(
            BidEnterpriseSnapshot.as_of <= current_time,
            BidEnterpriseSnapshot.frozen_at <= current_time,
            BidEnterpriseBusinessBaseline.reviewed_at <= current_time,
        )
    query = query.order_by(
        BidEnterpriseBusinessBaseline.reviewed_at.desc(),
        BidEnterpriseSnapshot.as_of.desc(),
        BidEnterpriseSnapshot.id.desc(),
    )
    if lock:
        query = query.with_for_update()
    pair = query.first()
    if pair is None:
        return None
    snapshot, baseline = pair
    source_hashes = dict(baseline.source_hashes_json or {})
    if source_hashes.get("snapshot_hash") != str(snapshot.snapshot_hash or ""):
        return None
    if bool(
        getattr(
            settings,
            "feature_bid_assessment_phase4_enterprise_evidence_import",
            False,
        )
    ):
        if not baseline.evidence_package_id or not baseline.evidence_package_hash:
            return None
        if validate_enterprise_evidence_package_at(
            db,
            package_id=str(baseline.evidence_package_id),
            expected_package_hash=str(baseline.evidence_package_hash),
            effective_at=(as_utc(effective_at) if effective_at is not None else _database_utc_now(db)),
        ) is None:
            return None
    if effective_at is not None:
        try:
            rows = validate_frozen_snapshot_metadata(db, snapshot)
            projection = project_enterprise_snapshot(
                snapshot,
                rows,
                include_values=False,
            )
        except BidEnterpriseCapabilityError:
            return None
        current_time = as_utc(effective_at)
        if any(
            _effective_record_status(record, as_of=current_time)
            in {"not_yet_valid", "expired", "missing"}
            for record in projection.get("records") or []
        ):
            return None
    return snapshot, baseline


def preview_enterprise_business_baseline(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
) -> dict[str, Any]:
    snapshot_id = str(command.get("snapshot_id") or "")
    evidence_package_id = str(command.get("evidence_package_id") or "").strip() or None
    review_note = str(command.get("review_note") or "").strip()
    reviewed_as_of_raw = command.get("reviewed_as_of")
    if not snapshot_id or not review_note or reviewed_as_of_raw is None:
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_COMMAND_INVALID"
        )
    try:
        if isinstance(reviewed_as_of_raw, datetime):
            reviewed_as_of = as_utc(reviewed_as_of_raw)
        else:
            reviewed_as_of = as_utc(
                datetime.fromisoformat(str(reviewed_as_of_raw).replace("Z", "+00:00"))
            )
    except (TypeError, ValueError) as exc:
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_REVIEW_TIME_INVALID"
        ) from exc
    database_now = _database_utc_now(db)
    if reviewed_as_of > database_now + timedelta(minutes=1) or reviewed_as_of < database_now - timedelta(minutes=15):
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_REVIEW_TIME_STALE"
        )
    reviews = _normalize_reviews(command)
    evidence_import_enabled = bool(
        getattr(
            settings,
            "feature_bid_assessment_phase4_enterprise_evidence_import",
            False,
        )
    )
    evidence_package: BidEnterpriseEvidencePackage | None = None
    if evidence_import_enabled:
        if evidence_package_id is None:
            raise BidEnterpriseBusinessBaselineError(
                "BID_ENTERPRISE_BUSINESS_EVIDENCE_PACKAGE_REQUIRED"
            )
        evidence_package = (
            db.query(BidEnterpriseEvidencePackage)
            .filter(
                BidEnterpriseEvidencePackage.id == evidence_package_id,
                BidEnterpriseEvidencePackage.status == "frozen",
            )
            .one_or_none()
        )
        if evidence_package is None:
            raise BidEnterpriseBusinessBaselineError(
                "BID_ENTERPRISE_BUSINESS_EVIDENCE_PACKAGE_INVALID"
            )
    snapshot = (
        db.query(BidEnterpriseSnapshot)
        .filter(BidEnterpriseSnapshot.id == snapshot_id)
        .one_or_none()
    )
    if snapshot is None or str(snapshot.status) != "frozen":
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_SNAPSHOT_NOT_FOUND"
        )
    latest_snapshot = _latest_frozen_snapshot(db, lock=False)
    try:
        rows = validate_frozen_snapshot_metadata(db, snapshot)
        snapshot_projection = project_enterprise_snapshot(
            snapshot,
            rows,
            include_values=True,
        )
    except BidEnterpriseCapabilityError as exc:
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_SNAPSHOT_INVALID"
        ) from exc
    record_map = {
        str(record["slot_code"]): record
        for record in snapshot_projection.get("records") or []
    }
    as_of = reviewed_as_of
    blocking_codes: list[str] = []
    follow_up_codes: list[str] = []
    reviewed_slots: list[dict[str, Any]] = []
    for review in reviews:
        code = review["slot_code"]
        record = record_map.get(code)
        effective_status = (
            _effective_record_status(record, as_of=as_of) if record is not None else "missing"
        )
        reasons: list[str] = []
        if review["disposition"] != "confirmed":
            reasons.append(
                "BUSINESS_CORRECTION_REQUIRED"
                if review["disposition"] == "correction_required"
                else "BUSINESS_REVIEW_REQUIRED"
            )
        if effective_status in {"supported", "partial"}:
            if review["evidence_class"] == "not_available" or not review["evidence_ref"]:
                reasons.append("BUSINESS_SOURCE_REFERENCE_REQUIRED")
            if (
                review["evidence_class"] in _HASHED_EVIDENCE_CLASSES
                and not review["evidence_hash"]
            ):
                reasons.append("BUSINESS_SOURCE_HASH_REQUIRED")
            if not record or str(record.get("source_status") or "") == "unknown":
                reasons.append("BUSINESS_GOVERNED_SOURCE_REQUIRED")
            if not record or not record.get("checked_at"):
                reasons.append("BUSINESS_SOURCE_CHECK_TIME_REQUIRED")
            if evidence_import_enabled and review["evidence_class"] in _HASHED_EVIDENCE_CLASSES:
                evidence_item_id = review.get("evidence_item_id")
                if not evidence_item_id or evidence_package_id is None:
                    reasons.append("BUSINESS_EVIDENCE_ITEM_REQUIRED")
                else:
                    try:
                        _, evidence_item = validate_package_item_reference(
                            db,
                            package_id=evidence_package_id,
                            slot_code=code,
                            evidence_item_id=str(evidence_item_id),
                        )
                    except BidEnterpriseEvidenceImportError:
                        reasons.append("BUSINESS_EVIDENCE_ITEM_NOT_IN_PACKAGE_SLOT")
                    else:
                        expected_ref = (
                            f"{evidence_item.source_record_id}@{evidence_item.source_version}"
                        )
                        if review["evidence_class"] != str(evidence_item.evidence_class):
                            reasons.append("BUSINESS_EVIDENCE_CLASS_MISMATCH")
                        if review["evidence_ref"] != expected_ref:
                            reasons.append("BUSINESS_EVIDENCE_REFERENCE_MISMATCH")
                        if review["evidence_hash"] != str(evidence_item.content_sha256):
                            reasons.append("BUSINESS_EVIDENCE_HASH_MISMATCH")
                        if (
                            evidence_item.valid_from is not None
                            and as_utc(evidence_item.valid_from) > as_of
                        ) or (
                            evidence_item.valid_to is not None
                            and as_utc(evidence_item.valid_to) < as_of
                        ):
                            reasons.append("BUSINESS_EVIDENCE_NOT_CURRENT")
        elif effective_status == "unknown":
            if review["evidence_class"] != "not_available" or not review["note"]:
                reasons.append("BUSINESS_UNKNOWN_REVIEW_NOTE_REQUIRED")
            if evidence_import_enabled and review.get("evidence_item_id"):
                reasons.append("BUSINESS_UNKNOWN_EVIDENCE_ITEM_FORBIDDEN")
        else:
            reasons.append("BUSINESS_SOURCE_NOT_CURRENT")
        blocking = bool(reasons)
        if review["disposition"] != "confirmed":
            blocking = True
        if blocking:
            blocking_codes.append(f"{code}_BUSINESS_REVIEW_BLOCKED")
        if (
            effective_status in {"partial", "unknown"}
            or review["evidence_class"] == "management_attestation"
            or str((record or {}).get("source_status") or "") == "self_reported"
        ):
            if not review["note"]:
                blocking_codes.append(f"{code}_FOLLOW_UP_NOTE_REQUIRED")
                reasons.append("BUSINESS_FOLLOW_UP_NOTE_REQUIRED")
            follow_up_codes.append(f"{code}_BUSINESS_FOLLOW_UP")
        reviewed_slots.append(
            {
                **review,
                "coverage_status": str((record or {}).get("coverage_status") or "missing"),
                "effective_status": effective_status,
                "source_status": str((record or {}).get("source_status") or "missing"),
                "source_record_id": str((record or {}).get("source_record_id") or "") or None,
                "source_version": str((record or {}).get("source_version") or "") or None,
                "record_payload_hash": str((record or {}).get("payload_hash") or "") or None,
                "review_ready": not blocking,
                "reason_codes": sorted(set(reasons)) or ["BUSINESS_BASELINE_SLOT_CONFIRMED"],
            }
        )
    if latest_snapshot is None or str(latest_snapshot.id) != snapshot_id:
        blocking_codes.append("BUSINESS_BASELINE_SNAPSHOT_NOT_LATEST")
    verification_outcome = (
        "verified_with_follow_up" if follow_up_codes else "verified"
    )
    source_hashes = {
        "snapshot_hash": str(snapshot.snapshot_hash or ""),
        "source_catalog_version": str(snapshot.source_catalog_version),
        "record_payload_hashes": {
            item["slot_code"]: item["record_payload_hash"] for item in reviewed_slots
        },
        "review_evidence_hashes": {
            item["slot_code"]: item["evidence_hash"] for item in reviewed_slots
        },
    }
    if evidence_import_enabled and evidence_package is not None:
        source_hashes["evidence_package_hash"] = str(evidence_package.package_hash)
    candidate_payload = {
        "schema": BUSINESS_BASELINE_VALIDATION_SCHEMA,
        "authority_version": BUSINESS_BASELINE_AUTHORITY,
        "snapshot_id": snapshot_id,
        "reviewed_as_of": _utc_text(reviewed_as_of),
        "reviewer_id": int(actor_id),
        "review_note": review_note,
        "slot_reviews": reviewed_slots,
        "source_hashes": source_hashes,
        "verification_outcome": verification_outcome,
    }
    if evidence_import_enabled and evidence_package_id is not None:
        candidate_payload["evidence_package_id"] = evidence_package_id
    candidate_hash = canonical_hash(candidate_payload)
    existing = (
        db.query(BidEnterpriseBusinessBaseline)
        .filter(BidEnterpriseBusinessBaseline.snapshot_id == snapshot_id)
        .one_or_none()
    )
    unique_blockers = sorted(set(blocking_codes))
    return {
        **candidate_payload,
        "blocking_codes": unique_blockers,
        "follow_up_codes": sorted(set(follow_up_codes)),
        "candidate_hash": candidate_hash,
        "can_freeze": not unique_blockers and existing is None,
        "already_frozen": existing is not None,
        "existing_business_baseline": _projection(existing) if existing is not None else None,
    }


def freeze_enterprise_business_baseline(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
    request_id: str,
    expected_candidate_hash: str,
    now: datetime | None = None,
) -> FrozenEnterpriseBusinessBaselineResult:
    snapshot_id = str(command.get("snapshot_id") or "")
    snapshot = (
        db.query(BidEnterpriseSnapshot)
        .filter(BidEnterpriseSnapshot.id == snapshot_id)
        .with_for_update()
        .one_or_none()
    )
    if snapshot is None:
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_SNAPSHOT_NOT_FOUND"
        )
    existing = (
        db.query(BidEnterpriseBusinessBaseline)
        .filter(BidEnterpriseBusinessBaseline.snapshot_id == snapshot_id)
        .with_for_update()
        .one_or_none()
    )
    preview = preview_enterprise_business_baseline(
        db,
        actor_id=actor_id,
        command=command,
    )
    normalized_expected = str(expected_candidate_hash or "").strip().lower()
    if normalized_expected != str(preview["candidate_hash"]):
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_CANDIDATE_HASH_MISMATCH"
        )
    if existing is not None:
        if str(existing.candidate_hash) != normalized_expected:
            raise BidEnterpriseBusinessBaselineError(
                "BID_ENTERPRISE_BUSINESS_ALREADY_FROZEN"
            )
        return FrozenEnterpriseBusinessBaselineResult(
            baseline=existing,
            created=False,
            projection=_projection(existing),
        )
    if not preview["can_freeze"]:
        raise BidEnterpriseBusinessBaselineError(
            "BID_ENTERPRISE_BUSINESS_NOT_READY"
        )
    current_time = as_utc(now) if now is not None else _database_utc_now(db)
    baseline_id = str(uuid.uuid5(_BASELINE_NAMESPACE, normalized_expected))
    version = f"enterprise-business-{current_time:%Y%m%d%H%M%S}-{normalized_expected[:12]}"
    manifest = {
        "schema": BUSINESS_BASELINE_SCHEMA,
        "authority_version": BUSINESS_BASELINE_AUTHORITY,
        "business_baseline_id": baseline_id,
        "version": version,
        "snapshot_id": snapshot_id,
        "snapshot_hash": str(snapshot.snapshot_hash or ""),
        "reviewer_id": int(actor_id),
        "reviewed_at": _utc_text(current_time),
        "verification_outcome": str(preview["verification_outcome"]),
        "candidate_hash": normalized_expected,
        "source_hashes": dict(preview["source_hashes"]),
    }
    if preview.get("evidence_package_id"):
        manifest["evidence_package_id"] = preview["evidence_package_id"]
        manifest["evidence_package_hash"] = dict(preview["source_hashes"]).get(
            "evidence_package_hash"
        )
    baseline_hash = canonical_hash(manifest)
    row = BidEnterpriseBusinessBaseline(
        id=baseline_id,
        version=version,
        snapshot_id=snapshot_id,
        evidence_package_id=preview.get("evidence_package_id"),
        evidence_package_hash=dict(preview["source_hashes"]).get(
            "evidence_package_hash"
        ),
        status="frozen",
        verification_outcome=str(preview["verification_outcome"]),
        reviewer_id=int(actor_id),
        review_note=str(command["review_note"]).strip(),
        slot_reviews_json=list(preview["slot_reviews"]),
        source_hashes_json=dict(preview["source_hashes"]),
        candidate_hash=normalized_expected,
        baseline_hash=baseline_hash,
        reviewed_at=current_time,
        created_at=current_time,
    )
    db.add(row)
    db.flush()
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=f"user:{actor_id}",
        action="bid.enterprise_business_baseline.freeze",
        entity_type="enterprise_business_baseline",
        entity_id=baseline_id,
        outcome="succeeded",
        request_id=request_id,
        after={
            "version": version,
            "snapshot_id": snapshot_id,
            "verification_outcome": str(preview["verification_outcome"]),
            "baseline_hash": baseline_hash,
        },
        metadata={"authority_version": BUSINESS_BASELINE_AUTHORITY},
        occurred_at=current_time,
    )
    return FrozenEnterpriseBusinessBaselineResult(
        baseline=row,
        created=True,
        projection=_projection(row),
    )
