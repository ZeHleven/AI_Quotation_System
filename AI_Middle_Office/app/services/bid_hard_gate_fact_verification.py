"""Phase 4D-3 governed fact verification and hard-gate comparability.

This authority does not parse source files or call a model.  A reviewer must
submit canonical values and bind every non-unknown value to either current,
citable tender Atoms or governed enterprise Evidence Items.  The immutable
baseline is assessment-scoped and becomes stale when any bound authority
changes.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
    BidManifestDocument,
)
from app.models.bid_assessment_config import BidEnterpriseSnapshot
from app.models.bid_assessment_documents import BidDocumentParseHead, BidEvidenceFragment
from app.models.bid_assessment_release import (
    BidEnterpriseBusinessBaseline,
    BidEnterpriseEvidenceItem,
    BidEnterpriseEvidencePackage,
    BidEnterpriseEvidencePackageItem,
    BidHardGateComparisonBaseline,
    BidHardGateComparisonEvidenceLink,
    BidFactComparisonLink,
)
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.models.bid_assessment_results import (
    BidFactAssertion,
    BidFactEvidenceLink,
    BidResolvedFact,
    BidResolvedFactHead,
)
from app.services.bid_assessment_eventing import (
    append_audit_log,
    as_utc,
    canonical_hash,
)


COMPARISON_BASELINE_SCHEMA = "bid.hard-gate.comparison-baseline.v1"
COMPARISON_VALIDATION_SCHEMA = "bid.hard-gate.comparison-baseline-validation.v1"
COMPARISON_AUTHORITY = "bid-hard-gate-fact-verification-v1"
_BASELINE_NAMESPACE = uuid.UUID("f4db66a6-3ed1-4d53-8fb9-acde2696ce1c")

COMPARABLE_FACT_SPECS: dict[str, dict[str, Any]] = {
    "tender.overview": {"side": "tender", "types": {"project_identity", "string"}},
    "tender.submission.deadline": {"side": "tender", "types": {"datetime"}},
    "tender.qualification.requirements": {"side": "tender", "types": {"requirement_list"}},
    "tender.guarantee.requirements": {"side": "tender", "types": {"requirement_list", "money"}},
    "tender.schedule.site_constraints": {"side": "tender", "types": {"requirement_list", "duration", "location"}},
    "enterprise.identity.legal_name": {"side": "enterprise", "types": {"enterprise_identity"}, "slot": "I01"},
    "enterprise.qualifications.active_records": {"side": "enterprise", "types": {"enterprise_record_list"}, "slot": "I02"},
    "enterprise.safety_license.active_record": {"side": "enterprise", "types": {"enterprise_record"}, "slot": "I03"},
    "enterprise.performance.records": {"side": "enterprise", "types": {"enterprise_record_list"}, "slot": "I04"},
    "enterprise.personnel.available_records": {"side": "enterprise", "types": {"enterprise_record_list"}, "slot": "I05"},
    "enterprise.financial.capacity": {"side": "enterprise", "types": {"enterprise_capacity"}, "slot": "I06"},
    "enterprise.guarantee.capacity": {"side": "enterprise", "types": {"enterprise_capacity"}, "slot": "I07"},
    "enterprise.bid_preparation.capacity": {"side": "enterprise", "types": {"enterprise_capacity"}, "slot": "I08"},
    "enterprise.prohibited_risk.rules": {"side": "enterprise", "types": {"enterprise_rule_list"}, "slot": "I09"},
    "enterprise.compliance.current_records": {"side": "enterprise", "types": {"enterprise_record"}, "slot": "I10"},
    "enterprise.client_risk.current_records": {"side": "enterprise", "types": {"enterprise_record_list"}, "slot": "I11"},
}


class BidHardGateFactVerificationError(RuntimeError):
    code = "BID_HARD_GATE_FACT_VERIFICATION_ERROR"


@dataclass(frozen=True)
class FrozenHardGateComparisonBaselineResult:
    baseline: BidHardGateComparisonBaseline
    created: bool
    projection: dict[str, Any]


def _database_utc_now(db: Session) -> datetime:
    value = db.execute(
        select(func.utc_timestamp(6))
        if db.get_bind().dialect.name == "mysql"
        else select(func.current_timestamp())
    ).scalar_one()
    if not isinstance(value, datetime):
        raise BidHardGateFactVerificationError("BID_DATABASE_TIME_INVALID")
    return as_utc(value)


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_review_time(raw: Any) -> datetime:
    try:
        parsed = raw if isinstance(raw, datetime) else datetime.fromisoformat(
            str(raw).replace("Z", "+00:00")
        )
        # MySQL DATETIME columns in the existing migration baseline do not
        # guarantee fractional-second retention.  Canonicalize reviewer time
        # to whole seconds so Candidate Hash remains reproducible after a DB
        # round trip on both SQLite and MySQL.
        return as_utc(parsed).replace(microsecond=0)
    except (TypeError, ValueError) as exc:
        raise BidHardGateFactVerificationError(
            "BID_HARD_GATE_COMPARISON_REVIEW_TIME_INVALID"
        ) from exc


def _projection(row: BidHardGateComparisonBaseline) -> dict[str, Any]:
    return {
        "schema": COMPARISON_BASELINE_SCHEMA,
        "comparison_baseline_id": str(row.id),
        "version": str(row.version),
        "assessment_id": str(row.assessment_id),
        "source_run_id": str(row.source_run_id),
        "scope_id": str(row.scope_id),
        "manifest_id": str(row.manifest_id),
        "enterprise_snapshot_id": str(row.enterprise_snapshot_id),
        "business_baseline_id": str(row.business_baseline_id),
        "evidence_package_id": str(row.evidence_package_id),
        "status": str(row.status),
        "verification_outcome": str(row.verification_outcome),
        "reviewer_id": int(row.reviewer_id),
        "review_note": str(row.review_note),
        "facts": list(row.facts_json or []),
        "source_hashes": dict(row.source_hashes_json or {}),
        "candidate_hash": str(row.candidate_hash),
        "baseline_hash": str(row.baseline_hash),
        "reviewed_at": _utc_text(row.reviewed_at),
    }


def get_hard_gate_comparison_baseline(
    db: Session,
    *,
    assessment_id: str | None = None,
) -> dict[str, Any] | None:
    query = db.query(BidHardGateComparisonBaseline).filter(
        BidHardGateComparisonBaseline.status == "frozen"
    )
    if assessment_id:
        query = query.filter(BidHardGateComparisonBaseline.assessment_id == assessment_id)
    row = query.order_by(
        BidHardGateComparisonBaseline.reviewed_at.desc(),
        BidHardGateComparisonBaseline.id.desc(),
    ).first()
    if row is None:
        return None
    projection = _projection(row)
    try:
        validate_hard_gate_comparison_baseline_at(
            db,
            baseline=row,
            effective_at=_database_utc_now(db),
        )
        projection["current"] = True
        projection["stale_code"] = None
    except BidHardGateFactVerificationError as exc:
        projection["current"] = False
        projection["stale_code"] = str(exc)
    return projection


def latest_hard_gate_comparison_baseline(
    db: Session,
    *,
    assessment_id: str,
    manifest_id: str,
    scope_id: str,
    business_baseline_id: str,
    effective_at: datetime,
    lock: bool = False,
) -> BidHardGateComparisonBaseline | None:
    query = db.query(BidHardGateComparisonBaseline).filter(
        BidHardGateComparisonBaseline.assessment_id == assessment_id,
        BidHardGateComparisonBaseline.manifest_id == manifest_id,
        BidHardGateComparisonBaseline.scope_id == scope_id,
        BidHardGateComparisonBaseline.business_baseline_id == business_baseline_id,
        BidHardGateComparisonBaseline.status == "frozen",
        BidHardGateComparisonBaseline.reviewed_at <= as_utc(effective_at),
    ).order_by(
        BidHardGateComparisonBaseline.reviewed_at.desc(),
        BidHardGateComparisonBaseline.id.desc(),
    )
    if lock:
        query = query.with_for_update()
    row = query.first()
    if row is None:
        return None
    try:
        validate_hard_gate_comparison_baseline_at(
            db,
            baseline=row,
            effective_at=effective_at,
        )
    except BidHardGateFactVerificationError:
        return None
    return row


def _normalize_fact_set(command: dict[str, Any]) -> list[dict[str, Any]]:
    rows = command.get("facts")
    if not isinstance(rows, list) or len(rows) != len(COMPARABLE_FACT_SPECS):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_FACT_SET_INVALID")
    normalized: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_FACT_INVALID")
        fact_slot = str(raw.get("fact_slot") or "")
        spec = COMPARABLE_FACT_SPECS.get(fact_slot)
        if spec is None or str(raw.get("source_side") or "") != spec["side"]:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_FACT_INVALID")
        status = str(raw.get("verification_status") or "")
        value_type = str(raw.get("value_type") or "").strip() or None
        value = raw.get("canonical_value")
        item_ids = sorted({str(value).strip() for value in raw.get("evidence_item_ids") or []})
        atom_ids = sorted({str(value).strip() for value in raw.get("evidence_atom_ids") or []})
        note = str(raw.get("note") or "").strip()
        if status not in {"supported", "partial", "unknown"} or not note:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_FACT_INVALID")
        if status == "unknown":
            if value is not None or value_type is not None or item_ids or atom_ids:
                raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_UNKNOWN_INVALID")
        else:
            if value is None or value_type not in set(spec["types"]):
                raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_INVALID")
            if spec["side"] == "tender" and (not atom_ids or item_ids):
                raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_TENDER_EVIDENCE_INVALID")
            if spec["side"] == "enterprise" and (not item_ids or atom_ids):
                raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ENTERPRISE_EVIDENCE_INVALID")
            _validate_machine_value(fact_slot, value_type, value, strict=status == "supported")
        fact_payload = {
            "fact_slot": fact_slot,
            "source_side": str(spec["side"]),
            "verification_status": status,
            "value_type": value_type,
            "canonical_value": value,
            "value_hash": canonical_hash(value) if value is not None else None,
            "evidence_item_ids": item_ids,
            "evidence_atom_ids": atom_ids,
            "note": note,
        }
        fact_payload["fact_hash"] = canonical_hash(fact_payload)
        normalized.append(fact_payload)
    normalized.sort(key=lambda item: str(item["fact_slot"]))
    if [item["fact_slot"] for item in normalized] != sorted(COMPARABLE_FACT_SPECS):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_FACT_SET_INVALID")
    return normalized


def _validate_machine_value(
    fact_slot: str,
    value_type: str,
    value: Any,
    *,
    strict: bool,
) -> None:
    if not strict:
        return
    if fact_slot == "tender.submission.deadline":
        if not isinstance(value, str):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE") from exc
        if parsed.tzinfo is None:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    if not isinstance(value, (dict, list)):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")

    def _number(raw: Any) -> Decimal | None:
        try:
            parsed = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return parsed if parsed.is_finite() and parsed >= 0 else None

    def _items(raw: Any, *keys: str) -> list[dict[str, Any]] | None:
        if isinstance(raw, list):
            return [dict(item) for item in raw] if all(
                isinstance(item, dict) for item in raw
            ) else None
        if not isinstance(raw, dict):
            return None
        for key in keys:
            rows = raw.get(key)
            if isinstance(rows, list) and all(isinstance(item, dict) for item in rows):
                return [dict(item) for item in rows]
        return [dict(raw)]

    def _contained_items(raw: Any, *keys: str) -> list[dict[str, Any]] | None:
        if isinstance(raw, list):
            return [dict(item) for item in raw] if all(
                isinstance(item, dict) for item in raw
            ) else None
        if not isinstance(raw, dict):
            return None
        for key in keys:
            rows = raw.get(key)
            if isinstance(rows, list) and all(isinstance(item, dict) for item in rows):
                return [dict(item) for item in rows]
        return None

    if fact_slot == "tender.overview":
        if not isinstance(value, dict) or not any(
            str(value.get(key) or "").strip()
            for key in ("procurer_name", "owner_name", "client_name", "counterparty")
        ):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    if fact_slot == "enterprise.identity.legal_name":
        if not isinstance(value, dict) or not str(value.get("legal_name") or "").strip():
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    if fact_slot == "enterprise.safety_license.active_record":
        status = str(value.get("status") or "").strip().lower() if isinstance(value, dict) else ""
        if status not in {"active", "valid", "正常", "有效", "blocked", "expired", "invalid", "异常", "无效"}:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    if fact_slot == "enterprise.compliance.current_records":
        status = str(value.get("status") or "").strip().lower() if isinstance(value, dict) else ""
        if status not in {"clear", "compliant", "eligible", "正常", "合规", "blocked", "noncompliant", "ineligible", "异常", "不合规"}:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    if fact_slot == "enterprise.financial.capacity":
        if not isinstance(value, dict) or _number(value.get("available_cash_cny")) is None:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    if fact_slot == "enterprise.guarantee.capacity":
        if not isinstance(value, dict) or not any(
            _number(value.get(key)) is not None
            for key in ("max_bond_cny", "available_cash_cny")
        ):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        forms = value.get("supported_forms", [])
        if not isinstance(forms, list) or any(not str(item).strip() for item in forms):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    if fact_slot == "enterprise.bid_preparation.capacity":
        if not isinstance(value, dict) or _number(value.get("available_person_days")) is None:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    if fact_slot == "tender.guarantee.requirements":
        rows = _items(value, "requirements", "items")
        if rows is None or not rows or not all(
            (
                item.get("not_applicable") is True
                or (
                    _number(
                        item.get("amount_cny")
                        or item.get("minimum_amount_cny")
                        or item.get("amount")
                    )
                    is not None
                    and str(item.get("currency") or "CNY").strip().upper() == "CNY"
                )
            )
            for item in rows
        ):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")
        return
    list_shapes = {
        "tender.qualification.requirements": ("requirements", "items"),
        "tender.schedule.site_constraints": ("requirements", "constraints", "items"),
        "enterprise.qualifications.active_records": ("records", "qualifications", "items"),
        "enterprise.performance.records": ("records", "projects", "items"),
        "enterprise.personnel.available_records": ("records", "people", "items"),
        "enterprise.prohibited_risk.rules": ("rules", "items"),
        "enterprise.client_risk.current_records": ("records", "risks", "items"),
    }
    keys = list_shapes.get(fact_slot)
    if keys is not None and _contained_items(value, *keys) is None:
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE")


def _current_source_authorities(
    db: Session,
    *,
    assessment_id: str,
    source_run_id: str,
    business_baseline_id: str,
) -> tuple[
    BidAssessment,
    BidAnalysisRun,
    BidDocumentManifest,
    BidAssessmentScope,
    BidEnterpriseSnapshot,
    BidEnterpriseBusinessBaseline,
    BidEnterpriseEvidencePackage,
]:
    assessment = db.query(BidAssessment).filter(BidAssessment.id == assessment_id).one_or_none()
    source_run = db.query(BidAnalysisRun).filter(
        BidAnalysisRun.id == source_run_id,
        BidAnalysisRun.assessment_id == assessment_id,
    ).one_or_none()
    if assessment is None or source_run is None or str(source_run.status) != "succeeded":
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_SOURCE_RUN_INVALID")
    manifest = db.query(BidDocumentManifest).filter(
        BidDocumentManifest.id == source_run.manifest_id,
        BidDocumentManifest.assessment_id == assessment_id,
    ).one_or_none()
    scope = db.query(BidAssessmentScope).filter(
        BidAssessmentScope.id == source_run.scope_id,
        BidAssessmentScope.assessment_id == assessment_id,
    ).one_or_none()
    if (
        manifest is None
        or scope is None
        or str(assessment.current_manifest_id or "") != str(manifest.id)
    ):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_SCOPE_STALE")
    latest_scope = db.query(BidAssessmentScope).filter(
        BidAssessmentScope.assessment_id == assessment_id
    ).order_by(BidAssessmentScope.version.desc(), BidAssessmentScope.id.desc()).first()
    if latest_scope is None or str(latest_scope.id) != str(scope.id):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_SCOPE_STALE")
    business = db.query(BidEnterpriseBusinessBaseline).filter(
        BidEnterpriseBusinessBaseline.id == business_baseline_id,
        BidEnterpriseBusinessBaseline.status == "frozen",
    ).one_or_none()
    if business is None or not business.evidence_package_id or not business.evidence_package_hash:
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_BUSINESS_BASELINE_INVALID")
    latest_business = db.query(BidEnterpriseBusinessBaseline).filter(
        BidEnterpriseBusinessBaseline.status == "frozen"
    ).order_by(
        BidEnterpriseBusinessBaseline.reviewed_at.desc(),
        BidEnterpriseBusinessBaseline.id.desc(),
    ).first()
    if latest_business is None or str(latest_business.id) != str(business.id):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_BUSINESS_BASELINE_STALE")
    snapshot = db.query(BidEnterpriseSnapshot).filter(
        BidEnterpriseSnapshot.id == business.snapshot_id,
        BidEnterpriseSnapshot.status == "frozen",
    ).one_or_none()
    package = db.query(BidEnterpriseEvidencePackage).filter(
        BidEnterpriseEvidencePackage.id == business.evidence_package_id,
        BidEnterpriseEvidencePackage.status == "frozen",
    ).one_or_none()
    if (
        snapshot is None
        or package is None
        or str(package.package_hash) != str(business.evidence_package_hash)
        or str(source_run.enterprise_snapshot_id) != str(snapshot.id)
    ):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ENTERPRISE_AUTHORITY_STALE")
    return assessment, source_run, manifest, scope, snapshot, business, package


def _validate_evidence(
    db: Session,
    *,
    facts: list[dict[str, Any]],
    manifest: BidDocumentManifest,
    package: BidEnterpriseEvidencePackage,
    reviewed_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    atom_ids = sorted({atom_id for fact in facts for atom_id in fact["evidence_atom_ids"]})
    item_ids = sorted({item_id for fact in facts for item_id in fact["evidence_item_ids"]})
    atoms: dict[str, BidEvidenceFragment] = {}
    if atom_ids:
        rows = db.query(BidEvidenceFragment).join(
            BidManifestDocument,
            (BidManifestDocument.document_version_id == BidEvidenceFragment.document_version_id)
            & (BidManifestDocument.manifest_id == manifest.id),
        ).join(
            BidDocumentParseHead,
            (BidDocumentParseHead.document_version_id == BidEvidenceFragment.document_version_id)
            & (BidDocumentParseHead.current_run_id == BidEvidenceFragment.parse_run_id),
        ).filter(BidEvidenceFragment.id.in_(tuple(atom_ids))).all()
        atoms = {str(row.id): row for row in rows}
        if set(atoms) != set(atom_ids):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ATOM_OUT_OF_SCOPE")
        for row in rows:
            locator = dict(row.locator_json or {})
            if locator.get("fragment_role") != "evidence_atom" or locator.get("is_citable") is not True:
                raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ATOM_NOT_CITABLE")
    items: dict[str, BidEnterpriseEvidenceItem] = {}
    item_slots: set[tuple[str, str]] = set()
    if item_ids:
        rows = db.query(BidEnterpriseEvidenceItem).filter(
            BidEnterpriseEvidenceItem.id.in_(tuple(item_ids)),
            BidEnterpriseEvidenceItem.status == "frozen",
        ).all()
        items = {str(row.id): row for row in rows}
        if set(items) != set(item_ids):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ITEM_OUT_OF_SCOPE")
        mappings = db.query(BidEnterpriseEvidencePackageItem).filter(
            BidEnterpriseEvidencePackageItem.package_id == package.id,
            BidEnterpriseEvidencePackageItem.evidence_item_id.in_(tuple(item_ids)),
        ).all()
        item_slots = {(str(row.evidence_item_id), str(row.slot_code)) for row in mappings}
    links: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}
    for fact in facts:
        fact_slot = str(fact["fact_slot"])
        if fact["verification_status"] == "unknown":
            continue
        spec = COMPARABLE_FACT_SPECS[fact_slot]
        if spec["side"] == "tender":
            for atom_id in fact["evidence_atom_ids"]:
                atom = atoms[atom_id]
                payload = {
                    "fact_slot": fact_slot,
                    "source_side": "tender",
                    "evidence_kind": "tender_atom",
                    "evidence_identity": atom_id,
                    "evidence_hash": str(atom.text_hash),
                    "locator_hash": str(atom.locator_hash),
                    "document_version_id": str(atom.document_version_id),
                    "parse_run_id": str(atom.parse_run_id),
                }
                payload["link_hash"] = canonical_hash(payload)
                links.append(payload)
                source_hashes[f"atom:{atom_id}"] = canonical_hash(
                    {"text_hash": str(atom.text_hash), "locator_hash": str(atom.locator_hash)}
                )
        else:
            slot_code = str(spec["slot"])
            for item_id in fact["evidence_item_ids"]:
                item = items[item_id]
                if (item_id, slot_code) not in item_slots:
                    raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ITEM_SLOT_MISMATCH")
                if (
                    (item.valid_from is not None and as_utc(item.valid_from) > reviewed_at)
                    or (item.valid_to is not None and as_utc(item.valid_to) < reviewed_at)
                ):
                    raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ITEM_NOT_CURRENT")
                payload = {
                    "fact_slot": fact_slot,
                    "source_side": "enterprise",
                    "evidence_kind": "enterprise_item",
                    "evidence_identity": item_id,
                    "evidence_hash": str(item.item_hash),
                    "locator_hash": None,
                    "document_version_id": None,
                    "parse_run_id": None,
                }
                payload["link_hash"] = canonical_hash(payload)
                links.append(payload)
                source_hashes[f"item:{item_id}"] = str(item.item_hash)
    links.sort(key=lambda item: (str(item["fact_slot"]), str(item["evidence_kind"]), str(item["evidence_identity"])))
    return links, source_hashes


def build_hard_gate_comparison_draft(
    db: Session,
    *,
    assessment_id: str,
    source_run_id: str,
    business_baseline_id: str,
) -> dict[str, Any]:
    """Project prior governed facts as partial reviewer candidates without writes."""

    _, source_run, _manifest, _scope, _snapshot, business, _package = (
        _current_source_authorities(
            db,
            assessment_id=assessment_id,
            source_run_id=source_run_id,
            business_baseline_id=business_baseline_id,
        )
    )
    resolved_rows = db.query(BidResolvedFact).join(
        BidResolvedFactHead,
        BidResolvedFactHead.resolved_fact_id == BidResolvedFact.id,
    ).filter(
        BidResolvedFactHead.run_id == source_run.id,
        BidResolvedFact.fact_slot.in_(tuple(COMPARABLE_FACT_SPECS)),
    ).order_by(
        BidResolvedFact.fact_slot.asc(),
        BidResolvedFact.scope_type.asc(),
    ).all()
    resolved_by_slot: dict[str, BidResolvedFact] = {}
    for row in resolved_rows:
        resolved_by_slot.setdefault(str(row.fact_slot), row)
    business_reviews = {
        str(item.get("slot_code") or ""): dict(item)
        for item in list(business.slot_reviews_json or [])
        if isinstance(item, dict)
    }
    facts: list[dict[str, Any]] = []
    for fact_slot, spec in sorted(COMPARABLE_FACT_SPECS.items()):
        fact = resolved_by_slot.get(fact_slot)
        candidate_ready = bool(
            fact is not None
            and str(fact.status) in {"supported", "partial"}
            and fact.value_json is not None
            and str(fact.value_type or "") in set(spec["types"])
        )
        atom_ids: list[str] = []
        item_ids: list[str] = []
        if candidate_ready and spec["side"] == "tender":
            assertion_ids = tuple(str(value) for value in fact.source_assertion_ids_json or [])
            if assertion_ids:
                atom_ids = sorted(
                    {
                        str(row[0])
                        for row in db.query(BidFactEvidenceLink.evidence_fragment_id)
                        .filter(BidFactEvidenceLink.assertion_id.in_(assertion_ids))
                        .all()
                    }
                )
            candidate_ready = bool(atom_ids)
        elif candidate_ready:
            review = business_reviews.get(str(spec["slot"]), {})
            evidence_item_id = str(review.get("evidence_item_id") or "").strip()
            if evidence_item_id:
                item_ids = [evidence_item_id]
            candidate_ready = bool(item_ids)
        facts.append(
            {
                "fact_slot": fact_slot,
                "source_side": str(spec["side"]),
                "verification_status": "partial" if candidate_ready else "unknown",
                "value_type": str(fact.value_type) if candidate_ready else None,
                "canonical_value": fact.value_json if candidate_ready else None,
                "evidence_item_ids": item_ids,
                "evidence_atom_ids": atom_ids,
                "note": (
                    "来自已完成 Run 的治理事实与当前权威证据，仅作为待人工确认候选。"
                    if candidate_ready
                    else "当前权威链未形成可核验候选，保持 unknown 并进入跟进项。"
                ),
            }
        )
    return {
        "schema": "bid.hard-gate.comparison-draft.v1",
        "assessment_id": assessment_id,
        "source_run_id": source_run_id,
        "business_baseline_id": business_baseline_id,
        "reviewed_as_of": _utc_text(_database_utc_now(db)),
        "review_note": (
            "逐项复核招标侧 Atom、企业侧 Evidence Item 与机器可比较值；"
            "partial 不会被硬门当作通过。"
        ),
        "facts": facts,
    }


def preview_hard_gate_comparison_baseline(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
) -> dict[str, Any]:
    if not getattr(settings, "feature_bid_assessment_phase4_fact_verification", False):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_FACT_VERIFICATION_DISABLED")
    assessment_id = str(command.get("assessment_id") or "")
    source_run_id = str(command.get("source_run_id") or "")
    business_baseline_id = str(command.get("business_baseline_id") or "")
    review_note = str(command.get("review_note") or "").strip()
    reviewed_at = _parse_review_time(command.get("reviewed_as_of"))
    database_now = _database_utc_now(db)
    if (
        not assessment_id
        or not source_run_id
        or not business_baseline_id
        or not review_note
        or reviewed_at > database_now + timedelta(minutes=1)
        or reviewed_at < database_now - timedelta(minutes=15)
    ):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_COMMAND_INVALID")
    facts = _normalize_fact_set(command)
    _, source_run, manifest, scope, snapshot, business, package = _current_source_authorities(
        db,
        assessment_id=assessment_id,
        source_run_id=source_run_id,
        business_baseline_id=business_baseline_id,
    )
    links, evidence_hashes = _validate_evidence(
        db,
        facts=facts,
        manifest=manifest,
        package=package,
        reviewed_at=reviewed_at,
    )
    status_counts = {
        status: sum(fact["verification_status"] == status for fact in facts)
        for status in ("supported", "partial", "unknown")
    }
    verification_outcome = (
        "verified" if status_counts["partial"] == 0 and status_counts["unknown"] == 0
        else "verified_with_follow_up"
    )
    source_hashes = {
        "manifest_hash": str(manifest.manifest_hash),
        "scope_hash": str(scope.scope_hash),
        "enterprise_snapshot_hash": str(snapshot.snapshot_hash),
        "business_baseline_hash": str(business.baseline_hash),
        "evidence_package_hash": str(package.package_hash),
        "evidence_hashes": evidence_hashes,
    }
    candidate_payload = {
        "schema": COMPARISON_VALIDATION_SCHEMA,
        "authority_version": COMPARISON_AUTHORITY,
        "assessment_id": assessment_id,
        "source_run_id": source_run_id,
        "scope_id": str(source_run.scope_id),
        "manifest_id": str(source_run.manifest_id),
        "enterprise_snapshot_id": str(snapshot.id),
        "business_baseline_id": str(business.id),
        "evidence_package_id": str(package.id),
        "reviewer_id": int(actor_id),
        "reviewed_as_of": _utc_text(reviewed_at),
        "review_note": review_note,
        "verification_outcome": verification_outcome,
        "status_counts": status_counts,
        "facts": facts,
        "source_hashes": source_hashes,
        "evidence_links": links,
    }
    candidate_hash = canonical_hash(candidate_payload)
    existing = db.query(BidHardGateComparisonBaseline).filter(
        BidHardGateComparisonBaseline.candidate_hash == candidate_hash
    ).one_or_none()
    return {
        **candidate_payload,
        "candidate_hash": candidate_hash,
        "blocking_codes": [],
        "follow_up_codes": [
            str(fact["fact_slot"])
            for fact in facts
            if fact["verification_status"] != "supported"
        ],
        "can_freeze": existing is None,
        "already_frozen": existing is not None,
        "existing_comparison_baseline": _projection(existing) if existing is not None else None,
    }


def freeze_hard_gate_comparison_baseline(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
    request_id: str,
    expected_candidate_hash: str,
    now: datetime | None = None,
) -> FrozenHardGateComparisonBaselineResult:
    preview = preview_hard_gate_comparison_baseline(db, actor_id=actor_id, command=command)
    normalized_expected = str(expected_candidate_hash or "").strip().lower()
    if normalized_expected != str(preview["candidate_hash"]):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_CANDIDATE_HASH_MISMATCH")
    existing = db.query(BidHardGateComparisonBaseline).filter(
        BidHardGateComparisonBaseline.candidate_hash == normalized_expected
    ).with_for_update().one_or_none()
    if existing is not None:
        return FrozenHardGateComparisonBaselineResult(
            baseline=existing,
            created=False,
            projection=_projection(existing),
        )
    current_time = as_utc(now) if now is not None else _database_utc_now(db)
    reviewed_at = _parse_review_time(preview["reviewed_as_of"])
    baseline_id = str(uuid.uuid5(_BASELINE_NAMESPACE, normalized_expected))
    version = f"hard-gate-comparison-{current_time:%Y%m%d%H%M%S}-{normalized_expected[:12]}"
    source_hashes = dict(preview["source_hashes"])
    manifest = {
        "schema": COMPARISON_BASELINE_SCHEMA,
        "authority_version": COMPARISON_AUTHORITY,
        "comparison_baseline_id": baseline_id,
        "version": version,
        "assessment_id": str(preview["assessment_id"]),
        "source_run_id": str(preview["source_run_id"]),
        "scope_id": str(preview["scope_id"]),
        "manifest_id": str(preview["manifest_id"]),
        "enterprise_snapshot_id": str(preview["enterprise_snapshot_id"]),
        "business_baseline_id": str(preview["business_baseline_id"]),
        "evidence_package_id": str(preview["evidence_package_id"]),
        "verification_outcome": str(preview["verification_outcome"]),
        "candidate_hash": normalized_expected,
        "source_hashes": source_hashes,
        "reviewed_at": _utc_text(reviewed_at),
    }
    baseline_hash = canonical_hash(manifest)
    row = BidHardGateComparisonBaseline(
        id=baseline_id,
        version=version,
        assessment_id=str(preview["assessment_id"]),
        source_run_id=str(preview["source_run_id"]),
        scope_id=str(preview["scope_id"]),
        manifest_id=str(preview["manifest_id"]),
        manifest_hash=str(source_hashes["manifest_hash"]),
        scope_hash=str(source_hashes["scope_hash"]),
        enterprise_snapshot_id=str(preview["enterprise_snapshot_id"]),
        enterprise_snapshot_hash=str(source_hashes["enterprise_snapshot_hash"]),
        business_baseline_id=str(preview["business_baseline_id"]),
        business_baseline_hash=str(source_hashes["business_baseline_hash"]),
        evidence_package_id=str(preview["evidence_package_id"]),
        evidence_package_hash=str(source_hashes["evidence_package_hash"]),
        status="frozen",
        verification_outcome=str(preview["verification_outcome"]),
        reviewer_id=int(actor_id),
        review_note=str(command["review_note"]).strip(),
        facts_json=list(preview["facts"]),
        source_hashes_json=source_hashes,
        candidate_hash=normalized_expected,
        baseline_hash=baseline_hash,
        reviewed_at=reviewed_at,
        created_at=current_time,
    )
    db.add(row)
    db.flush()
    for link in preview["evidence_links"]:
        kind = str(link["evidence_kind"])
        identity = str(link["evidence_identity"])
        db.add(
            BidHardGateComparisonEvidenceLink(
                id=str(uuid.uuid4()),
                comparison_baseline_id=baseline_id,
                fact_slot=str(link["fact_slot"]),
                source_side=str(link["source_side"]),
                evidence_kind=kind,
                evidence_identity=identity,
                evidence_item_id=identity if kind == "enterprise_item" else None,
                evidence_fragment_id=identity if kind == "tender_atom" else None,
                document_version_id=link.get("document_version_id"),
                parse_run_id=link.get("parse_run_id"),
                evidence_hash=str(link["evidence_hash"]),
                locator_hash=link.get("locator_hash"),
                link_hash=str(link["link_hash"]),
                created_at=current_time,
            )
        )
    db.flush()
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=f"user:{actor_id}",
        action="bid.hard_gate_comparison_baseline.freeze",
        entity_type="hard_gate_comparison_baseline",
        entity_id=baseline_id,
        assessment_id=str(row.assessment_id),
        outcome="succeeded",
        request_id=request_id,
        after={
            "version": version,
            "source_run_id": str(row.source_run_id),
            "verification_outcome": str(row.verification_outcome),
            "baseline_hash": baseline_hash,
        },
        metadata={"authority_version": COMPARISON_AUTHORITY},
        occurred_at=current_time,
    )
    return FrozenHardGateComparisonBaselineResult(
        baseline=row,
        created=True,
        projection=_projection(row),
    )


def validate_hard_gate_comparison_baseline_at(
    db: Session,
    *,
    baseline: BidHardGateComparisonBaseline,
    effective_at: datetime,
) -> None:
    current_time = as_utc(effective_at)
    _, source_run, manifest, scope, snapshot, business, package = _current_source_authorities(
        db,
        assessment_id=str(baseline.assessment_id),
        source_run_id=str(baseline.source_run_id),
        business_baseline_id=str(baseline.business_baseline_id),
    )
    if (
        str(source_run.manifest_id) != str(baseline.manifest_id)
        or str(source_run.scope_id) != str(baseline.scope_id)
        or str(manifest.manifest_hash) != str(baseline.manifest_hash)
        or str(scope.scope_hash) != str(baseline.scope_hash)
        or str(snapshot.id) != str(baseline.enterprise_snapshot_id)
        or str(snapshot.snapshot_hash) != str(baseline.enterprise_snapshot_hash)
        or str(business.baseline_hash) != str(baseline.business_baseline_hash)
        or str(package.id) != str(baseline.evidence_package_id)
        or str(package.package_hash) != str(baseline.evidence_package_hash)
    ):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_AUTHORITY_DRIFT")
    facts = list(baseline.facts_json or [])
    if len(facts) != len(COMPARABLE_FACT_SPECS):
        raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_FACT_SET_INVALID")
    for fact in facts:
        if not isinstance(fact, dict):
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_FACT_INVALID")
        fact_without_hash = dict(fact)
        stored_fact_hash = str(fact_without_hash.pop("fact_hash", ""))
        if not stored_fact_hash or canonical_hash(fact_without_hash) != stored_fact_hash:
            raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_FACT_HASH_INVALID")
    expected_links, expected_evidence_hashes = _validate_evidence(
        db,
        facts=facts,
        manifest=manifest,
        package=package,
        reviewed_at=current_time,
    )
    links = db.query(BidHardGateComparisonEvidenceLink).filter(
        BidHardGateComparisonEvidenceLink.comparison_baseline_id == baseline.id
    ).all()
    if {str(link.link_hash) for link in links} != {
        str(link["link_hash"]) for link in expected_links
    }:
        raise BidHardGateFactVerificationError(
            "BID_HARD_GATE_COMPARISON_EVIDENCE_SET_STALE"
        )
    for link in links:
        if str(link.evidence_kind) == "enterprise_item":
            item = db.query(BidEnterpriseEvidenceItem).filter(
                BidEnterpriseEvidenceItem.id == link.evidence_item_id,
                BidEnterpriseEvidenceItem.status == "frozen",
            ).one_or_none()
            if (
                item is None
                or str(item.item_hash) != str(link.evidence_hash)
                or (item.valid_from is not None and as_utc(item.valid_from) > current_time)
                or (item.valid_to is not None and as_utc(item.valid_to) < current_time)
            ):
                raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ITEM_STALE")
        else:
            atom = db.query(BidEvidenceFragment).join(
                BidManifestDocument,
                (BidManifestDocument.document_version_id == BidEvidenceFragment.document_version_id)
                & (BidManifestDocument.manifest_id == baseline.manifest_id),
            ).join(
                BidDocumentParseHead,
                (BidDocumentParseHead.document_version_id == BidEvidenceFragment.document_version_id)
                & (BidDocumentParseHead.current_run_id == BidEvidenceFragment.parse_run_id),
            ).filter(BidEvidenceFragment.id == link.evidence_fragment_id).one_or_none()
            locator = dict(atom.locator_json or {}) if atom is not None else {}
            if (
                atom is None
                or str(atom.text_hash) != str(link.evidence_hash)
                or str(atom.locator_hash) != str(link.locator_hash)
                or locator.get("fragment_role") != "evidence_atom"
                or locator.get("is_citable") is not True
            ):
                raise BidHardGateFactVerificationError("BID_HARD_GATE_COMPARISON_ATOM_STALE")
    expected_source_hashes = {
        "manifest_hash": str(manifest.manifest_hash),
        "scope_hash": str(scope.scope_hash),
        "enterprise_snapshot_hash": str(snapshot.snapshot_hash),
        "business_baseline_hash": str(business.baseline_hash),
        "evidence_package_hash": str(package.package_hash),
        "evidence_hashes": expected_evidence_hashes,
    }
    if dict(baseline.source_hashes_json or {}) != expected_source_hashes:
        raise BidHardGateFactVerificationError(
            "BID_HARD_GATE_COMPARISON_SOURCE_HASHES_STALE"
        )
    status_counts = {
        status: sum(
            str(fact.get("verification_status") or "") == status for fact in facts
        )
        for status in ("supported", "partial", "unknown")
    }
    candidate_payload = {
        "schema": COMPARISON_VALIDATION_SCHEMA,
        "authority_version": COMPARISON_AUTHORITY,
        "assessment_id": str(baseline.assessment_id),
        "source_run_id": str(baseline.source_run_id),
        "scope_id": str(baseline.scope_id),
        "manifest_id": str(baseline.manifest_id),
        "enterprise_snapshot_id": str(baseline.enterprise_snapshot_id),
        "business_baseline_id": str(baseline.business_baseline_id),
        "evidence_package_id": str(baseline.evidence_package_id),
        "reviewer_id": int(baseline.reviewer_id),
        "reviewed_as_of": _utc_text(baseline.reviewed_at),
        "review_note": str(baseline.review_note),
        "verification_outcome": str(baseline.verification_outcome),
        "status_counts": status_counts,
        "facts": facts,
        "source_hashes": expected_source_hashes,
        "evidence_links": expected_links,
    }
    if canonical_hash(candidate_payload) != str(baseline.candidate_hash):
        raise BidHardGateFactVerificationError(
            "BID_HARD_GATE_COMPARISON_CANDIDATE_HASH_INVALID"
        )
    manifest_payload = {
        "schema": COMPARISON_BASELINE_SCHEMA,
        "authority_version": COMPARISON_AUTHORITY,
        "comparison_baseline_id": str(baseline.id),
        "version": str(baseline.version),
        "assessment_id": str(baseline.assessment_id),
        "source_run_id": str(baseline.source_run_id),
        "scope_id": str(baseline.scope_id),
        "manifest_id": str(baseline.manifest_id),
        "enterprise_snapshot_id": str(baseline.enterprise_snapshot_id),
        "business_baseline_id": str(baseline.business_baseline_id),
        "evidence_package_id": str(baseline.evidence_package_id),
        "verification_outcome": str(baseline.verification_outcome),
        "candidate_hash": str(baseline.candidate_hash),
        "source_hashes": expected_source_hashes,
        "reviewed_at": _utc_text(baseline.reviewed_at),
    }
    if canonical_hash(manifest_payload) != str(baseline.baseline_hash):
        raise BidHardGateFactVerificationError(
            "BID_HARD_GATE_COMPARISON_BASELINE_HASH_INVALID"
        )


def materialize_hard_gate_comparison_facts(
    db: Session,
    *,
    run: BidAnalysisRun,
    task_id: str,
    attempt_id: str,
    current_time: datetime,
) -> dict[str, Any]:
    """Materialize only the facts frozen in the Run-pinned comparison baseline."""

    baseline_id = str(run.hard_gate_comparison_baseline_id or "")
    baseline_hash = str(run.hard_gate_comparison_baseline_hash or "")
    if not baseline_id or not baseline_hash:
        raise BidHardGateFactVerificationError(
            "BID_HARD_GATE_COMPARISON_RUN_BINDING_MISSING"
        )
    baseline = db.query(BidHardGateComparisonBaseline).filter(
        BidHardGateComparisonBaseline.id == baseline_id,
        BidHardGateComparisonBaseline.assessment_id == run.assessment_id,
        BidHardGateComparisonBaseline.status == "frozen",
    ).one_or_none()
    if baseline is None or str(baseline.baseline_hash) != baseline_hash:
        raise BidHardGateFactVerificationError(
            "BID_HARD_GATE_COMPARISON_RUN_BINDING_STALE"
        )
    validate_hard_gate_comparison_baseline_at(
        db,
        baseline=baseline,
        effective_at=as_utc(run.evaluation_time),
    )
    evidence_by_slot: dict[str, list[BidHardGateComparisonEvidenceLink]] = {}
    for link in db.query(BidHardGateComparisonEvidenceLink).filter(
        BidHardGateComparisonEvidenceLink.comparison_baseline_id == baseline.id
    ).order_by(
        BidHardGateComparisonEvidenceLink.fact_slot.asc(),
        BidHardGateComparisonEvidenceLink.evidence_identity.asc(),
    ).all():
        evidence_by_slot.setdefault(str(link.fact_slot), []).append(link)

    assertion_ids: list[str] = []
    unknown_slots: list[str] = []
    for fact in list(baseline.facts_json or []):
        fact_slot = str(fact.get("fact_slot") or "")
        verification_status = str(fact.get("verification_status") or "")
        if fact_slot not in COMPARABLE_FACT_SPECS:
            raise BidHardGateFactVerificationError(
                "BID_HARD_GATE_COMPARISON_FACT_SET_INVALID"
            )
        if verification_status == "unknown":
            unknown_slots.append(fact_slot)
            continue
        if verification_status not in {"supported", "partial"}:
            raise BidHardGateFactVerificationError(
                "BID_HARD_GATE_COMPARISON_FACT_INVALID"
            )
        evidence_links = evidence_by_slot.get(fact_slot, [])
        expected_kind = (
            "tender_atom"
            if COMPARABLE_FACT_SPECS[fact_slot]["side"] == "tender"
            else "enterprise_item"
        )
        if not evidence_links or any(
            str(link.evidence_kind) != expected_kind for link in evidence_links
        ):
            raise BidHardGateFactVerificationError(
                "BID_HARD_GATE_COMPARISON_FACT_EVIDENCE_MISSING"
            )
        assertion_payload = {
            "authority_version": COMPARISON_AUTHORITY,
            "run_id": str(run.id),
            "task_id": str(task_id),
            "attempt_id": str(attempt_id),
            "comparison_baseline_id": baseline_id,
            "comparison_baseline_hash": baseline_hash,
            "fact_slot": fact_slot,
            "fact_hash": str(fact.get("fact_hash") or ""),
            "verification_status": verification_status,
            "value_type": str(fact.get("value_type") or ""),
            "value_hash": str(fact.get("value_hash") or ""),
            "asserted_at": _utc_text(run.evaluation_time),
        }
        assertion_hash = canonical_hash(assertion_payload)
        assertion = db.query(BidFactAssertion).filter(
            BidFactAssertion.run_id == run.id,
            BidFactAssertion.assertion_hash == assertion_hash,
        ).one_or_none()
        if assertion is None:
            reason_codes = [
                "HARD_GATE_COMPARISON_BASELINE_FROZEN",
                (
                    "HARD_GATE_COMPARISON_SUPPORTED"
                    if verification_status == "supported"
                    else "HARD_GATE_COMPARISON_PARTIAL"
                ),
            ]
            assertion = BidFactAssertion(
                id=str(uuid.uuid4()),
                assessment_id=str(run.assessment_id),
                run_id=str(run.id),
                task_id=str(task_id),
                source_task_attempt_id=str(attempt_id),
                model_result_id=None,
                fact_catalog_version_id=str(run.fact_catalog_version_id),
                fact_slot=fact_slot,
                scope_type="assessment",
                scope_id=str(run.assessment_id),
                value_type=str(fact["value_type"]),
                value_json=fact["canonical_value"],
                value_hash=str(fact["value_hash"]),
                source_type=(
                    "document"
                    if COMPARABLE_FACT_SPECS[fact_slot]["side"] == "tender"
                    else "enterprise"
                ),
                confidence="high" if verification_status == "supported" else "medium",
                status="accepted",
                asserted_at=as_utc(run.evaluation_time),
                assertion_hash=assertion_hash,
                reason_codes_json=reason_codes,
                created_at=current_time,
            )
            db.add(assertion)
            db.flush()
        comparison_link = db.query(BidFactComparisonLink).filter(
            BidFactComparisonLink.assertion_id == assertion.id
        ).one_or_none()
        if comparison_link is None:
            link_payload = {
                "assertion_id": str(assertion.id),
                "comparison_baseline_id": baseline_id,
                "fact_slot": fact_slot,
                "fact_hash": str(fact["fact_hash"]),
            }
            db.add(
                BidFactComparisonLink(
                    assertion_id=str(assertion.id),
                    comparison_baseline_id=baseline_id,
                    fact_slot=fact_slot,
                    fact_hash=str(fact["fact_hash"]),
                    link_hash=canonical_hash(link_payload),
                    created_at=current_time,
                )
            )
        if expected_kind == "tender_atom":
            for source_link in evidence_links:
                existing = db.query(BidFactEvidenceLink).filter(
                    BidFactEvidenceLink.assertion_id == assertion.id,
                    BidFactEvidenceLink.evidence_fragment_id
                    == source_link.evidence_fragment_id,
                ).one_or_none()
                if existing is not None:
                    continue
                evidence_payload = {
                    "assertion_id": str(assertion.id),
                    "evidence_id": str(source_link.evidence_fragment_id),
                    "manifest_id": str(run.manifest_id),
                    "parse_run_id": str(source_link.parse_run_id),
                    "text_hash": str(source_link.evidence_hash),
                    "locator_hash": str(source_link.locator_hash),
                    "comparison_baseline_id": baseline_id,
                }
                db.add(
                    BidFactEvidenceLink(
                        assertion_id=str(assertion.id),
                        evidence_fragment_id=str(source_link.evidence_fragment_id),
                        manifest_id=str(run.manifest_id),
                        parse_run_id=str(source_link.parse_run_id),
                        document_version_id=str(source_link.document_version_id),
                        evidence_text_hash=str(source_link.evidence_hash),
                        locator_hash=str(source_link.locator_hash),
                        context_read=True,
                        link_hash=canonical_hash(evidence_payload),
                        created_at=current_time,
                    )
                )
        assertion_ids.append(str(assertion.id))
    db.flush()
    return {
        "schema": "bid.hard-gate.comparable-fact-materialization.v1",
        "authority_version": COMPARISON_AUTHORITY,
        "run_id": str(run.id),
        "task_id": str(task_id),
        "comparison_baseline_id": baseline_id,
        "comparison_baseline_hash": baseline_hash,
        "assertion_ids": sorted(assertion_ids),
        "unknown_fact_slots": sorted(unknown_slots),
        "materialized_count": len(assertion_ids),
    }
