"""MVP-1 deterministic Fact, hard-gate, Claim, Decision and Report authority."""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidManifestDocument,
)
from app.models.bid_assessment_documents import BidDocumentParseHead, BidEvidenceFragment
from app.models.bid_assessment_results import (
    BidClaimCitation,
    BidFactAssertion,
    BidFactCoverage,
    BidFactEvidenceLink,
    BidFactEnterpriseLink,
    BidHardGateResult,
    BidPreliminaryDecision,
    BidPreliminaryReport,
    BidReportClaim,
    BidReportValidation,
    BidResolvedFact,
    BidResolvedFactHead,
)
from app.models.bid_assessment_runtime import BidAnalysisRun, BidCheckpoint, BidTask
from app.models.bid_assessment_release import (
    BidFactComparisonLink,
    BidHardGateComparisonBaseline,
)
from app.models.bid_assessment_tooling import BidToolInvocation, BidToolResult
from app.models.bid_model_execution import BidModelResult
from app.services.bid_assessment_eventing import (
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_task_runtime import TaskLeaseClaim, lock_task_claim


FACT_AUTHORITY_VERSION = "bid-fact-authority-mvp1-v1"
DECISION_AUTHORITY_VERSION = "bid-preliminary-decision-mvp1-v1"
CLAIM_VALIDATOR_VERSION = "bid-claim-evidence-validator-mvp1-v1"
REPORT_RENDERER_VERSION = "bid-preliminary-report-mvp1-v1"


class BidMvp1AuthorityError(RuntimeError):
    code = "BID_MVP1_AUTHORITY_ERROR"


@dataclass(frozen=True)
class AuthorityOutput:
    output_ref: str
    output_hash: str
    payload: dict[str, Any]


CATALOG_ROOT = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "bid_assessment"
    / "v1"
)
LEGACY_CATALOG_PATH = CATALOG_ROOT / "fact-catalog-mvp1.json"
PHASE4C_CATALOG_PATH = CATALOG_ROOT / "fact-catalog-mvp1-phase4c1.json"


@lru_cache(maxsize=2)
def _load_mvp1_fact_catalog(path_text: str) -> dict[str, Any]:
    payload = json.loads(Path(path_text).read_text(encoding="utf-8"))
    slots = payload.get("slots")
    if payload.get("schema") != "bid.fact.catalog.mvp1.v1" or not isinstance(slots, list):
        raise BidMvp1AuthorityError("BID_MVP1_FACT_CATALOG_INVALID")
    return payload


def load_mvp1_fact_catalog() -> dict[str, Any]:
    path = (
        PHASE4C_CATALOG_PATH
        if settings.feature_bid_assessment_phase4_enterprise_capability
        else LEGACY_CATALOG_PATH
    )
    return _load_mvp1_fact_catalog(str(path))


def _catalog_index() -> dict[str, dict[str, Any]]:
    return {str(item["slot"]): dict(item) for item in load_mvp1_fact_catalog()["slots"]}


def _output(prefix: str, identifier: str, payload: dict[str, Any]) -> AuthorityOutput:
    normalized = dict(payload)
    normalized["authority_version"] = normalized.get("authority_version") or prefix
    output_hash = canonical_hash(normalized)
    return AuthorityOutput(
        output_ref=f"{prefix}:{identifier}",
        output_hash=output_hash,
        payload=normalized,
    )


def _run_scope(db: Session, run: BidAnalysisRun, scope_type: str) -> str:
    if scope_type == "assessment":
        return str(run.assessment_id)
    scope = (
        db.query(BidAssessmentScope)
        .filter(
            BidAssessmentScope.id == run.scope_id,
            BidAssessmentScope.assessment_id == run.assessment_id,
        )
        .one()
    )
    snapshot = dict(scope.selected_lot_snapshot_json or {})
    lot_id = snapshot.get("lot_id") or scope.source_lot_candidate_id
    if not lot_id:
        raise BidMvp1AuthorityError("BID_FACT_LOT_SCOPE_UNAVAILABLE")
    return str(lot_id)


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scoped_evidence(
    db: Session,
    *,
    run: BidAnalysisRun,
    evidence_ids: list[str],
) -> dict[str, BidEvidenceFragment]:
    normalized = sorted(set(str(value) for value in evidence_ids))
    if not normalized:
        return {}
    rows = (
        db.query(BidEvidenceFragment)
        .join(
            BidManifestDocument,
            (BidManifestDocument.document_version_id == BidEvidenceFragment.document_version_id)
            & (BidManifestDocument.manifest_id == run.manifest_id),
        )
        .join(
            BidDocumentParseHead,
            (BidDocumentParseHead.document_version_id == BidEvidenceFragment.document_version_id)
            & (BidDocumentParseHead.current_run_id == BidEvidenceFragment.parse_run_id),
        )
        .filter(BidEvidenceFragment.id.in_(tuple(normalized)))
        .all()
    )
    by_id = {str(row.id): row for row in rows}
    if set(by_id) != set(normalized):
        raise BidMvp1AuthorityError("BID_FACT_EVIDENCE_OUT_OF_SCOPE")
    for row in rows:
        locator = dict(row.locator_json or {})
        fragment_role = locator.get("fragment_role")
        if fragment_role is not None and not (
            fragment_role == "evidence_atom"
            and locator.get("is_citable") is True
        ):
            raise BidMvp1AuthorityError("BID_FACT_EVIDENCE_ROLE_NOT_CITABLE")
    return by_id


def _candidate_lineage(
    db: Session,
    *,
    task: BidTask,
    attempt_id: str,
    model_result_id: str,
    action_type: str,
) -> tuple[BidModelResult, set[str]]:
    """Bind a candidate to the current persisted graph state.

    A model/tool operation releases the lease and the task resumes with a new
    Attempt/Fence.  The final accepting Attempt therefore must not be confused
    with the historical Attempt that produced the immutable ModelResult.
    """
    result = (
        db.query(BidModelResult)
        .filter(
            BidModelResult.id == model_result_id,
            BidModelResult.task_id == task.id,
            BidModelResult.action_type == action_type,
            BidModelResult.storage_kind == "inline",
        )
        .one_or_none()
    )
    checkpoint = (
        db.query(BidCheckpoint)
        .filter(BidCheckpoint.task_attempt_id == attempt_id)
        .order_by(BidCheckpoint.action_seq.desc(), BidCheckpoint.created_at.desc())
        .first()
    )
    expected_ref = f"model-result:{model_result_id}"
    state = dict(checkpoint.state_json or {}) if checkpoint is not None else {}
    if (
        result is None
        or checkpoint is None
        or canonical_hash(state) != str(checkpoint.state_hash)
        or str(checkpoint.candidate_output_ref or "") != expected_ref
        or expected_ref not in set(str(value) for value in state.get("candidate_refs") or [])
    ):
        raise BidMvp1AuthorityError("BID_MODEL_CANDIDATE_CHECKPOINT_LINEAGE_INVALID")
    observed_result_ids = {
        str(value).split(":", 1)[1]
        for value in state.get("observed_tool_result_refs") or []
        if str(value).startswith("tool-result:")
    }
    return result, observed_result_ids


def _context_read_evidence(
    db: Session,
    *,
    task_id: str,
    observed_result_ids: set[str],
) -> set[str]:
    if not observed_result_ids:
        return set()
    rows = (
        db.query(BidToolResult.evidence_refs_json)
        .join(BidToolInvocation, BidToolInvocation.id == BidToolResult.invocation_id)
        .filter(
            BidToolResult.id.in_(tuple(sorted(observed_result_ids))),
            BidToolInvocation.task_id == task_id,
            BidToolInvocation.tool_name == "evidence.read",
            BidToolInvocation.status == "succeeded",
            BidToolResult.status.in_(("ok", "partial")),
        )
        .all()
    )
    return {
        str(evidence_id)
        for (references,) in rows
        for evidence_id in (references or [])
    }


def persist_fact_candidates(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    model_result_id: str,
    now: datetime | None = None,
) -> AuthorityOutput:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = lock_task_claim(db, claim, now=current_time)
    result, observed_result_ids = _candidate_lineage(
        db,
        task=task,
        attempt_id=str(attempt.id),
        model_result_id=model_result_id,
        action_type="submit_fact_candidates",
    )
    action = dict(result.action_json or {})
    candidates = list(action.get("candidates") or [])
    if not candidates:
        raise BidMvp1AuthorityError("BID_FACT_CANDIDATES_EMPTY")
    catalog = _catalog_index()
    context_read = _context_read_evidence(
        db, task_id=str(task.id), observed_result_ids=observed_result_ids
    )
    assertion_ids: list[str] = []
    for candidate in candidates:
        value = dict(candidate)
        fact_slot = str(value.get("fact_slot") or "")
        catalog_slot = catalog.get(fact_slot)
        if catalog_slot is None or str(catalog_slot.get("task_type")) != str(task.task_type):
            raise BidMvp1AuthorityError("BID_FACT_SLOT_NOT_BOUND_TO_TASK")
        value_type = str(value.get("value_type") or "")
        if value_type not in set(catalog_slot.get("value_types") or []):
            raise BidMvp1AuthorityError("BID_FACT_VALUE_TYPE_NOT_ALLOWED")
        scope = dict(value.get("scope") or {})
        scope_type = str(scope.get("type") or "")
        scope_id = str(scope.get("id") or "")
        if scope_type not in {"assessment", "lot"} or scope_id != _run_scope(
            db, run, scope_type
        ):
            raise BidMvp1AuthorityError("BID_FACT_SCOPE_INVALID")
        source_type = str(value.get("source_type") or "")
        if source_type != "document":
            raise BidMvp1AuthorityError("BID_FACT_EXTRACTION_SOURCE_INVALID")
        evidence_ids = sorted(set(str(item) for item in value.get("evidence_ids") or []))
        evidence = _scoped_evidence(db, run=run, evidence_ids=evidence_ids)
        if not evidence_ids or not set(evidence_ids) <= context_read:
            raise BidMvp1AuthorityError("BID_FACT_EVIDENCE_CONTEXT_NOT_READ")
        asserted_at = _parse_utc(value.get("asserted_at"))
        if asserted_at is None:
            raise BidMvp1AuthorityError("BID_FACT_ASSERTED_AT_INVALID")
        if asserted_at != as_utc(run.evaluation_time):
            raise BidMvp1AuthorityError("BID_FACT_ASSERTED_AT_NOT_FROZEN")
        value_hash = canonical_hash(value.get("value"))
        assertion_payload = {
            "authority_version": FACT_AUTHORITY_VERSION,
            "run_id": str(run.id),
            "task_id": str(task.id),
            "source_task_attempt_id": str(result.source_task_attempt_id),
            "accepted_by_attempt_id": str(attempt.id),
            "model_result_id": str(result.id),
            "fact_slot": fact_slot,
            "scope": {"type": scope_type, "id": scope_id},
            "value_type": value_type,
            "value_hash": value_hash,
            "source_type": source_type,
            "confidence": str(value.get("confidence") or ""),
            "evidence_ids": evidence_ids,
            "asserted_at": asserted_at,
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
        if assertion is None:
            assertion = BidFactAssertion(
                id=str(uuid.uuid4()),
                assessment_id=str(run.assessment_id),
                run_id=str(run.id),
                task_id=str(task.id),
                source_task_attempt_id=str(result.source_task_attempt_id),
                model_result_id=str(result.id),
                fact_catalog_version_id=str(run.fact_catalog_version_id),
                fact_slot=fact_slot,
                scope_type=scope_type,
                scope_id=scope_id,
                value_type=value_type,
                value_json=value.get("value"),
                value_hash=value_hash,
                source_type=source_type,
                confidence=str(value.get("confidence")),
                status="accepted",
                asserted_at=asserted_at,
                assertion_hash=assertion_hash,
                reason_codes_json=list(action.get("reason_codes") or []) + ["EVIDENCE_SCOPE_VALIDATED"],
                created_at=current_time,
            )
            db.add(assertion)
            db.flush()
            for evidence_id in evidence_ids:
                fragment = evidence[evidence_id]
                link_payload = {
                    "assertion_id": str(assertion.id),
                    "evidence_id": evidence_id,
                    "manifest_id": str(run.manifest_id),
                    "parse_run_id": str(fragment.parse_run_id),
                    "text_hash": str(fragment.text_hash),
                    "locator_hash": str(fragment.locator_hash),
                }
                db.add(
                    BidFactEvidenceLink(
                        assertion_id=str(assertion.id),
                        evidence_fragment_id=evidence_id,
                        manifest_id=str(run.manifest_id),
                        parse_run_id=str(fragment.parse_run_id),
                        document_version_id=str(fragment.document_version_id),
                        evidence_text_hash=str(fragment.text_hash),
                        locator_hash=str(fragment.locator_hash),
                        context_read=True,
                        link_hash=canonical_hash(link_payload),
                        created_at=current_time,
                    )
                )
        assertion_ids.append(str(assertion.id))
    db.flush()
    payload = {
        "authority_version": FACT_AUTHORITY_VERSION,
        "run_id": str(run.id),
        "task_id": str(task.id),
        "model_result_id": str(result.id),
        "assertion_ids": sorted(set(assertion_ids)),
        "accepted_count": len(set(assertion_ids)),
    }
    return _output("fact-assertions", str(result.id), payload)


def build_fact_coverage_baseline(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    now: datetime | None = None,
) -> AuthorityOutput:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    _attempt, task, run = lock_task_claim(db, claim, now=current_time)
    created: list[str] = []
    for slot in sorted(_catalog_index()):
        row = (
            db.query(BidFactCoverage)
            .filter(BidFactCoverage.run_id == run.id, BidFactCoverage.fact_slot == slot)
            .one_or_none()
        )
        if row is None:
            coverage_payload = {
                "run_id": str(run.id),
                "fact_slot": slot,
                "status": "not_assessed",
                "assertion_count": 0,
                "reason_codes": ["MVP1_BASELINE_INITIALIZED"],
            }
            row = BidFactCoverage(
                id=str(uuid.uuid4()),
                run_id=str(run.id),
                fact_slot=slot,
                status="not_assessed",
                assertion_count=0,
                reason_codes_json=coverage_payload["reason_codes"],
                coverage_hash=canonical_hash(coverage_payload),
                created_at=current_time,
                updated_at=current_time,
            )
            db.add(row)
        created.append(str(row.id))
    db.flush()
    return _output(
        "fact-coverage",
        str(run.id),
        {"run_id": str(run.id), "task_id": str(task.id), "coverage_ids": created},
    )


def resolve_facts(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    now: datetime | None = None,
) -> AuthorityOutput:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    _attempt, task, run = lock_task_claim(db, claim, now=current_time)
    comparison_facts: dict[str, dict[str, Any]] = {}
    if run.hard_gate_comparison_baseline_id:
        comparison_baseline = db.query(BidHardGateComparisonBaseline).filter(
            BidHardGateComparisonBaseline.id == run.hard_gate_comparison_baseline_id,
            BidHardGateComparisonBaseline.status == "frozen",
        ).one_or_none()
        if (
            comparison_baseline is None
            or str(comparison_baseline.baseline_hash)
            != str(run.hard_gate_comparison_baseline_hash or "")
        ):
            raise BidMvp1AuthorityError(
                "BID_HARD_GATE_COMPARISON_RUN_BINDING_STALE"
            )
        comparison_facts = {
            str(item["fact_slot"]): dict(item)
            for item in list(comparison_baseline.facts_json or [])
            if isinstance(item, dict) and item.get("fact_slot")
        }
    catalog_index = _catalog_index()
    # Phase 4D-3 freezes a deliberately small comparison vocabulary that is
    # newer than the historical extraction catalog.  A Run-bound comparison
    # fact must therefore be resolved even when the old catalog has no task
    # definition for that slot.  With no comparison baseline this remains the
    # exact legacy catalog iteration.
    fact_slots = sorted(set(catalog_index) | set(comparison_facts))
    resolved_ids: list[str] = []
    for fact_slot in fact_slots:
        catalog_slot = catalog_index.get(fact_slot, {})
        all_assertions = (
            db.query(BidFactAssertion)
            .filter(
                BidFactAssertion.run_id == run.id,
                BidFactAssertion.fact_slot == fact_slot,
                BidFactAssertion.status == "accepted",
            )
            .order_by(BidFactAssertion.asserted_at.asc(), BidFactAssertion.id.asc())
            .all()
        )
        comparison_fact = comparison_facts.get(fact_slot)
        ignored_candidate_count = 0
        if comparison_fact is not None:
            if str(comparison_fact.get("verification_status")) == "unknown":
                assertions = []
            else:
                assertions = (
                    db.query(BidFactAssertion)
                    .join(
                        BidFactComparisonLink,
                        BidFactComparisonLink.assertion_id == BidFactAssertion.id,
                    )
                    .filter(
                        BidFactAssertion.run_id == run.id,
                        BidFactAssertion.fact_slot == fact_slot,
                        BidFactAssertion.status == "accepted",
                        BidFactComparisonLink.comparison_baseline_id
                        == run.hard_gate_comparison_baseline_id,
                        BidFactComparisonLink.fact_slot == fact_slot,
                    )
                    .order_by(
                        BidFactAssertion.asserted_at.asc(),
                        BidFactAssertion.id.asc(),
                    )
                    .all()
                )
            ignored_candidate_count = max(0, len(all_assertions) - len(assertions))
        else:
            assertions = all_assertions
        value_hashes = sorted(set(str(row.value_hash) for row in assertions))
        if comparison_fact is not None and str(
            comparison_fact.get("verification_status")
        ) == "unknown":
            status, value_type, value_json, reasons = (
                "unknown",
                None,
                None,
                ["VERIFIED_COMPARISON_EXPLICIT_UNKNOWN"],
            )
        elif not assertions:
            status, value_type, value_json, reasons = "unknown", None, None, ["NO_ACCEPTED_ASSERTION"]
        elif len(value_hashes) == 1:
            partial = any(
                bool(
                    {
                        "ENTERPRISE_RECORD_PARTIAL",
                        "HARD_GATE_COMPARISON_PARTIAL",
                    }
                    & set(row.reason_codes_json or [])
                )
                for row in assertions
            )
            status = "partial" if partial else "supported"
            value_type = str(assertions[0].value_type)
            value_json = assertions[0].value_json
            reasons = [
                "VERIFIED_COMPARISON_PARTIAL"
                if partial and comparison_fact is not None
                else "CONSISTENT_PARTIAL_ENTERPRISE_ASSERTIONS"
                if partial
                else "VERIFIED_COMPARISON_SUPPORTED"
                if comparison_fact is not None
                else "CONSISTENT_ACCEPTED_ASSERTIONS"
            ]
        else:
            status, value_type, value_json, reasons = "conflicted", None, None, ["CONFLICTING_ACCEPTED_ASSERTIONS"]
        if comparison_fact is not None and ignored_candidate_count:
            reasons.append("VERIFIED_COMPARISON_OVERRIDES_CANDIDATE_ASSERTIONS")
        if assertions:
            scope_type = str(assertions[0].scope_type)
        elif comparison_fact is not None or str(catalog_slot.get("task_type")) in {
            "enterprise_governed_read",
            "build_enterprise_snapshot",
        }:
            scope_type = "assessment"
        else:
            scope_type = "lot"
        scope_id = (
            str(assertions[0].scope_id)
            if assertions
            else _run_scope(db, run, scope_type)
        )
        resolution_payload = {
            "authority_version": FACT_AUTHORITY_VERSION,
            "run_id": str(run.id),
            "fact_slot": fact_slot,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "status": status,
            "value_type": value_type,
            "value_hash": canonical_hash(value_json) if value_json is not None else None,
            "assertion_ids": [str(row.id) for row in assertions],
            "reason_codes": reasons,
        }
        resolution_hash = canonical_hash(resolution_payload)
        resolved = (
            db.query(BidResolvedFact)
            .filter(
                BidResolvedFact.run_id == run.id,
                BidResolvedFact.fact_slot == fact_slot,
                BidResolvedFact.scope_type == scope_type,
                BidResolvedFact.scope_id == scope_id,
                BidResolvedFact.resolution_hash == resolution_hash,
            )
            .one_or_none()
        )
        if resolved is None:
            resolved = BidResolvedFact(
                id=str(uuid.uuid4()),
                run_id=str(run.id),
                fact_slot=fact_slot,
                scope_type=scope_type,
                scope_id=scope_id,
                status=status,
                value_type=value_type,
                value_json=value_json,
                source_assertion_ids_json=[str(row.id) for row in assertions],
                reason_codes_json=reasons,
                resolution_hash=resolution_hash,
                created_at=current_time,
            )
            db.add(resolved)
            db.flush()
        head = (
            db.query(BidResolvedFactHead)
            .filter(
                BidResolvedFactHead.run_id == run.id,
                BidResolvedFactHead.fact_slot == fact_slot,
                BidResolvedFactHead.scope_type == scope_type,
                BidResolvedFactHead.scope_id == scope_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if head is None:
            head = BidResolvedFactHead(
                run_id=str(run.id),
                fact_slot=fact_slot,
                scope_type=scope_type,
                scope_id=scope_id,
                resolved_fact_id=str(resolved.id),
                row_version=1,
                created_at=current_time,
                updated_at=current_time,
            )
            db.add(head)
        elif str(head.resolved_fact_id) != str(resolved.id):
            head.resolved_fact_id = str(resolved.id)
            head.row_version = int(head.row_version) + 1
            head.updated_at = current_time
        coverage = (
            db.query(BidFactCoverage)
            .filter(BidFactCoverage.run_id == run.id, BidFactCoverage.fact_slot == fact_slot)
            .with_for_update()
            .one_or_none()
        )
        coverage_status = {
            "supported": "resolved",
            "partial": "resolved",
            "conflicted": "conflicted",
        }.get(status, "missing")
        coverage_payload = {
            "run_id": str(run.id),
            "fact_slot": fact_slot,
            "status": coverage_status,
            "assertion_count": len(assertions),
            "resolved_fact_id": str(resolved.id),
            "reason_codes": reasons,
        }
        if coverage is None:
            coverage = BidFactCoverage(
                id=str(uuid.uuid4()), run_id=str(run.id), fact_slot=fact_slot,
                status=coverage_status, assertion_count=len(assertions),
                reason_codes_json=reasons, coverage_hash=canonical_hash(coverage_payload),
                created_at=current_time, updated_at=current_time,
            )
            db.add(coverage)
        else:
            coverage.status = coverage_status
            coverage.assertion_count = len(assertions)
            coverage.reason_codes_json = reasons
            coverage.coverage_hash = canonical_hash(coverage_payload)
            coverage.updated_at = current_time
        resolved_ids.append(str(resolved.id))
    db.flush()
    return _output(
        "resolved-facts",
        str(run.id),
        {"run_id": str(run.id), "task_id": str(task.id), "resolved_fact_ids": resolved_ids},
    )


GATE_AUTHORITY_VERSION = "bid-hard-gate-phase4c2-v3"
GATE_DEFINITIONS = {
    "evaluate_deadline_gate": ("HG01", "block"),
    "evaluate_qualification_gate": ("HG02", "block"),
    "evaluate_personnel_performance_gate": ("HG03", "block"),
    "evaluate_legal_compliance_gate": ("HG04", "block"),
    "evaluate_guarantee_cash_gate": ("HG05", "block"),
    "evaluate_minimum_bid_capacity_gate": ("HG06", "block"),
    "evaluate_enterprise_prohibited_risk_gate": ("HG07", "block"),
}
GATE_ACCEPTANCE_SPECS: dict[str, dict[str, Any]] = {
    "HG01": {
        "label": "投标截止时间",
        "fact_slots": ("tender.submission.deadline",),
        "enterprise_slot_codes": (),
    },
    "HG02": {
        "label": "企业资质与安全许可",
        "fact_slots": (
            "tender.qualification.requirements",
            "enterprise.qualifications.active_records",
            "enterprise.safety_license.active_record",
        ),
        "enterprise_slot_codes": ("I02", "I03"),
    },
    "HG03": {
        "label": "业绩与人员能力",
        "fact_slots": (
            "tender.qualification.requirements",
            "enterprise.performance.records",
            "enterprise.personnel.available_records",
        ),
        "enterprise_slot_codes": ("I04", "I05"),
    },
    "HG04": {
        "label": "企业当前合规",
        "fact_slots": ("enterprise.compliance.current_records",),
        "enterprise_slot_codes": ("I10",),
    },
    "HG05": {
        "label": "保证金与资金能力",
        "fact_slots": (
            "tender.guarantee.requirements",
            "enterprise.financial.capacity",
            "enterprise.guarantee.capacity",
        ),
        "enterprise_slot_codes": ("I06", "I07"),
    },
    "HG06": {
        "label": "投标准备能力",
        "fact_slots": (
            "tender.schedule.site_constraints",
            "enterprise.bid_preparation.capacity",
        ),
        "enterprise_slot_codes": ("I08",),
    },
    "HG07": {
        "label": "禁投与客户风险",
        "fact_slots": (
            "tender.overview",
            "enterprise.prohibited_risk.rules",
            "enterprise.client_risk.current_records",
        ),
        "enterprise_slot_codes": ("I09", "I11"),
    },
}
LEGACY_GATE_SLOTS = {
    "HG02": "enterprise.qualification.match",
    "HG03": "enterprise.personnel_performance.match",
    "HG04": "enterprise.legal_compliance.match",
    "HG05": "enterprise.guarantee_cash.capacity",
    "HG06": "enterprise.minimum_bid.capacity",
    "HG07": "enterprise.prohibited_risk.present",
}


def _machine_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s\-_—–·/\\:：,，.。()（）\[\]【】]+", "", normalized)


def _decimal_value(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, dict) and set(value) >= {"amount", "currency"}:
        if value.get("currency") != "CNY":
            return None
        value = value.get("amount")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _structured_items(value: Any, *container_keys: str) -> list[dict[str, Any]] | None:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)] if all(
            isinstance(item, dict) for item in value
        ) else None
    if not isinstance(value, dict):
        return None
    for key in container_keys:
        items = value.get(key)
        if isinstance(items, list) and all(isinstance(item, dict) for item in items):
            return [dict(item) for item in items]
    return [dict(value)]


def _item_tokens(item: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in (
        "code", "requirement_code", "qualification_code", "certificate_code",
        "name", "role",
    ):
        token = _machine_token(item.get(key))
        if token:
            tokens.add(token)
    for key in (
        "codes", "qualifications", "certificates", "roles", "categories",
        "supported_forms", "forms",
    ):
        values = item.get(key)
        if isinstance(values, list):
            tokens.update(
                token for token in (_machine_token(value) for value in values) if token
            )
    return tokens


def _requirement_kind(item: dict[str, Any]) -> str:
    return _machine_token(
        item.get("requirement_type") or item.get("kind") or item.get("category")
    )


def _resolved_fact_map(db: Session, run_id: str) -> dict[str, BidResolvedFact]:
    rows = (
        db.query(BidResolvedFact)
        .join(BidResolvedFactHead, BidResolvedFactHead.resolved_fact_id == BidResolvedFact.id)
        .filter(BidResolvedFactHead.run_id == run_id)
        .order_by(BidResolvedFact.fact_slot.asc(), BidResolvedFact.scope_type.asc())
        .all()
    )
    result: dict[str, BidResolvedFact] = {}
    for row in rows:
        result.setdefault(str(row.fact_slot), row)
    return result


def _fact_supported(fact: BidResolvedFact | None) -> bool:
    return fact is not None and str(fact.status) == "supported"


def _gate_summary(
    *,
    mode: str,
    compared: int = 0,
    matched: int = 0,
    mismatched: int = 0,
    indeterminate: int = 0,
) -> dict[str, Any]:
    return {
        "comparison_mode": mode,
        "compared_item_count": int(compared),
        "matched_item_count": int(matched),
        "mismatched_item_count": int(mismatched),
        "indeterminate_item_count": int(indeterminate),
    }


def _legacy_gate_result(
    gate_code: str,
    facts: dict[str, BidResolvedFact],
) -> tuple[str, list[str], list[BidResolvedFact], dict[str, Any]]:
    slot = LEGACY_GATE_SLOTS.get(gate_code)
    fact = facts.get(str(slot)) if slot else None
    if not _fact_supported(fact) or not isinstance(fact.value_json, bool):
        return "unknown", ["STRUCTURED_GATE_INPUT_INCOMPLETE"], [], _gate_summary(mode="structured_v1")
    matched = not bool(fact.value_json) if gate_code == "HG07" else bool(fact.value_json)
    return (
        "pass" if matched else "fail",
        ["LEGACY_GOVERNED_BOOLEAN_MATCH" if matched else "LEGACY_GOVERNED_BOOLEAN_MISMATCH"],
        [fact],
        _gate_summary(mode="legacy_boolean_v1", compared=1, matched=int(matched), mismatched=int(not matched)),
    )


def _gate_acceptance_projection(
    gate_code: str,
    *,
    status: str,
    facts: dict[str, BidResolvedFact],
) -> dict[str, Any]:
    spec = GATE_ACCEPTANCE_SPECS[gate_code]
    required_fact_slots = list(spec["fact_slots"])
    unresolved_fact_slots = [
        slot
        for slot in required_fact_slots
        if not _fact_supported(facts.get(slot))
    ]
    explanations = {
        "pass": "当前冻结招标事实与企业能力满足该项确定性比较。",
        "fail": "当前冻结事实存在确定性不满足项，需要负责人决定是否停止投入。",
        "unknown": "当前证据或企业能力数据不足，系统不会把该项推断为通过。",
        "not_applicable": "当前招标要求明确表明该项不适用。",
    }
    next_actions: list[str] = []
    if status == "unknown":
        if any(slot.startswith("enterprise.") for slot in unresolved_fact_slots):
            next_actions.append("补齐或更新对应企业能力槽，并冻结新的企业基线版本。")
        if any(slot.startswith("tender.") for slot in unresolved_fact_slots):
            next_actions.append("补齐招标资料结构化事实或进行人工复核。")
        if not next_actions:
            next_actions.append("复核不可机器比较的字段结构与原始证据。")
    elif status == "fail":
        next_actions.extend(
            [
                "复核未满足项、企业数据有效期和招标原文证据。",
                "由负责人确认终止、澄清或采取补救方案。",
            ]
        )
    return {
        "label": str(spec["label"]),
        "explanation": explanations.get(status, "等待进一步复核。"),
        "required_fact_slots": required_fact_slots,
        "unresolved_fact_slots": unresolved_fact_slots,
        "enterprise_slot_codes": list(spec["enterprise_slot_codes"]),
        "next_actions": next_actions,
    }


def _compare_requirement_records(
    requirement_fact: BidResolvedFact | None,
    enterprise_fact: BidResolvedFact | None,
    *,
    kinds: set[str],
    enterprise_keys: tuple[str, ...],
) -> tuple[str, list[str], list[BidResolvedFact], dict[str, Any]] | None:
    if not _fact_supported(requirement_fact):
        return None
    requirements = _structured_items(requirement_fact.value_json, "requirements", "items")
    if requirements is None:
        return None
    selected = [item for item in requirements if _requirement_kind(item) in kinds]
    if not selected:
        return None
    if not _fact_supported(enterprise_fact):
        return (
            "unknown",
            ["ENTERPRISE_CAPABILITY_FACT_NOT_SUPPORTED"],
            [requirement_fact],
            _gate_summary(
                mode="structured_exact_set_v1",
                indeterminate=len(selected),
            ),
        )
    records = _structured_items(enterprise_fact.value_json, *enterprise_keys)
    if records is None:
        return (
            "unknown",
            ["ENTERPRISE_CAPABILITY_RECORDS_NOT_COMPARABLE"],
            [requirement_fact, enterprise_fact],
            _gate_summary(
                mode="structured_exact_set_v1",
                indeterminate=len(selected),
            ),
        )
    available = set().union(*(_item_tokens(item) for item in records)) if records else set()
    matched = mismatched = indeterminate = 0
    for item in selected:
        required = _item_tokens(item)
        if not required:
            indeterminate += 1
        elif required <= available:
            matched += 1
        else:
            mismatched += 1
    compared = len(selected)
    summary = _gate_summary(
        mode="structured_exact_set_v1", compared=compared, matched=matched,
        mismatched=mismatched, indeterminate=indeterminate,
    )
    inputs = [requirement_fact, enterprise_fact]
    if mismatched:
        return "fail", ["ENTERPRISE_CAPABILITY_REQUIREMENT_MISMATCH"], inputs, summary
    if indeterminate:
        return "unknown", ["ENTERPRISE_CAPABILITY_REQUIREMENT_NOT_COMPARABLE"], inputs, summary
    return "pass", ["ENTERPRISE_CAPABILITY_REQUIREMENTS_MATCHED"], inputs, summary


def _combine_gate_results(
    results: list[tuple[str, list[str], list[BidResolvedFact], dict[str, Any]]],
    *,
    pass_reason: str,
    fail_reason: str,
    unknown_reason: str,
) -> tuple[str, list[str], list[BidResolvedFact], dict[str, Any]]:
    statuses = [item[0] for item in results]
    inputs = [fact for item in results for fact in item[2]]
    summary = _gate_summary(
        mode="structured_exact_set_v1",
        compared=sum(item[3]["compared_item_count"] for item in results),
        matched=sum(item[3]["matched_item_count"] for item in results),
        mismatched=sum(item[3]["mismatched_item_count"] for item in results),
        indeterminate=sum(item[3]["indeterminate_item_count"] for item in results),
    )
    if "fail" in statuses:
        return "fail", [fail_reason], inputs, summary
    if "unknown" in statuses:
        return "unknown", [unknown_reason], inputs, summary
    return "pass", [pass_reason], inputs, summary


def _gate_compare(
    gate_code: str,
    facts: dict[str, BidResolvedFact],
    run: BidAnalysisRun,
) -> tuple[str, list[str], list[BidResolvedFact], dict[str, Any]]:
    if gate_code == "HG01":
        fact = facts.get("tender.submission.deadline")
        if not _fact_supported(fact):
            return "unknown", ["REQUIRED_FACT_NOT_SUPPORTED"], [], _gate_summary(mode="datetime_v1")
        deadline = _parse_utc(fact.value_json)
        if deadline is None:
            return "unknown", ["DEADLINE_NOT_MACHINE_COMPARABLE"], [fact], _gate_summary(mode="datetime_v1", indeterminate=1)
        passed = deadline > as_utc(run.evaluation_time)
        return (
            "pass" if passed else "fail",
            ["SUBMISSION_DEADLINE_AFTER_EVALUATION_TIME" if passed else "SUBMISSION_DEADLINE_NOT_AFTER_EVALUATION_TIME"],
            [fact],
            _gate_summary(mode="datetime_v1", compared=1, matched=int(passed), mismatched=int(not passed)),
        )

    requirements = facts.get("tender.qualification.requirements")
    if gate_code == "HG02":
        qualification = _compare_requirement_records(
            requirements,
            facts.get("enterprise.qualifications.active_records"),
            kinds={"qualification", "qualificationcertificate", "资质", "企业资质"},
            enterprise_keys=("records", "qualifications", "items"),
        )
        results = [qualification] if qualification is not None else []
        if _fact_supported(requirements):
            requirement_items = _structured_items(requirements.value_json, "requirements", "items")
            safety_items = [] if requirement_items is None else [
                item for item in requirement_items
                if _requirement_kind(item) in {"safetylicense", "safety", "安全生产许可证", "安全许可证"}
            ]
            if safety_items:
                safety = facts.get("enterprise.safety_license.active_record")
                if _fact_supported(safety) and isinstance(safety.value_json, dict):
                    active = _machine_token(safety.value_json.get("status")) in {"active", "valid", "正常", "有效"}
                    results.append((
                        "pass" if active else "fail",
                        ["SAFETY_LICENSE_ACTIVE" if active else "SAFETY_LICENSE_NOT_ACTIVE"],
                        [requirements, safety],
                        _gate_summary(mode="safety_license_status_v1", compared=1, matched=int(active), mismatched=int(not active)),
                    ))
                else:
                    results.append((
                        "unknown", ["SAFETY_LICENSE_NOT_SUPPORTED"], [requirements],
                        _gate_summary(mode="safety_license_status_v1", indeterminate=1),
                    ))
        if results:
            return _combine_gate_results(
                results,
                pass_reason="QUALIFICATION_AND_SAFETY_REQUIREMENTS_MATCHED",
                fail_reason="QUALIFICATION_OR_SAFETY_REQUIREMENT_MISMATCH",
                unknown_reason="QUALIFICATION_OR_SAFETY_NOT_COMPARABLE",
            )
    elif gate_code == "HG03":
        performance = _compare_requirement_records(
            requirements,
            facts.get("enterprise.performance.records"),
            kinds={"performance", "projectperformance", "业绩", "项目业绩"},
            enterprise_keys=("records", "projects", "items"),
        )
        personnel = _compare_requirement_records(
            requirements,
            facts.get("enterprise.personnel.available_records"),
            kinds={"personnel", "staff", "projectmanager", "人员", "项目经理"},
            enterprise_keys=("records", "people", "items"),
        )
        results = [item for item in (performance, personnel) if item is not None]
        if results:
            return _combine_gate_results(
                results,
                pass_reason="PERSONNEL_AND_PERFORMANCE_REQUIREMENTS_MATCHED",
                fail_reason="PERSONNEL_OR_PERFORMANCE_REQUIREMENT_MISMATCH",
                unknown_reason="PERSONNEL_OR_PERFORMANCE_NOT_COMPARABLE",
            )
    elif gate_code == "HG04":
        fact = facts.get("enterprise.compliance.current_records")
        if _fact_supported(fact) and isinstance(fact.value_json, dict):
            status = _machine_token(fact.value_json.get("status"))
            if status in {"clear", "compliant", "eligible", "正常", "合规"}:
                return "pass", ["ENTERPRISE_COMPLIANCE_CURRENT_AND_CLEAR"], [fact], _gate_summary(mode="compliance_status_v1", compared=1, matched=1)
            if status in {"blocked", "noncompliant", "ineligible", "异常", "不合规"}:
                return "fail", ["ENTERPRISE_COMPLIANCE_BLOCKING_STATUS"], [fact], _gate_summary(mode="compliance_status_v1", compared=1, mismatched=1)
            return "unknown", ["ENTERPRISE_COMPLIANCE_STATUS_NOT_COMPARABLE"], [fact], _gate_summary(mode="compliance_status_v1", indeterminate=1)
    elif gate_code == "HG05":
        tender = facts.get("tender.guarantee.requirements")
        capacity = facts.get("enterprise.guarantee.capacity")
        financial = facts.get("enterprise.financial.capacity")
        if _fact_supported(tender) and _fact_supported(capacity):
            required: Decimal | None = _decimal_value(tender.value_json)
            required_form = ""
            if required is None:
                items = _structured_items(tender.value_json, "requirements", "items")
                if items is not None and any(item.get("not_applicable") is True for item in items):
                    return "not_applicable", ["TENDER_GUARANTEE_NOT_APPLICABLE"], [tender], _gate_summary(mode="guarantee_capacity_v1")
                if items is not None:
                    amounts = [
                        _decimal_value(item.get("amount_cny") or item.get("minimum_amount_cny") or item.get("amount"))
                        for item in items
                    ]
                    amounts = [value for value in amounts if value is not None]
                    if amounts:
                        required = max(amounts)
                    forms = [_machine_token(item.get("form")) for item in items]
                    required_form = next((value for value in forms if value), "")
            cap_value = capacity.value_json if isinstance(capacity.value_json, dict) else {}
            max_bond = _decimal_value(cap_value.get("max_bond_cny"))
            available_cash = _decimal_value(cap_value.get("available_cash_cny"))
            if available_cash is None and _fact_supported(financial) and isinstance(financial.value_json, dict):
                available_cash = _decimal_value(financial.value_json.get("available_cash_cny"))
            supported_forms = {_machine_token(value) for value in cap_value.get("supported_forms", []) if _machine_token(value)} if isinstance(cap_value.get("supported_forms", []), list) else set()
            comparable_capacity = max(value for value in (max_bond, available_cash) if value is not None) if any(value is not None for value in (max_bond, available_cash)) else None
            inputs = [fact for fact in (tender, capacity, financial) if fact is not None]
            if required is not None and comparable_capacity is not None:
                amount_ok = comparable_capacity >= required
                form_ok = not required_form or required_form in supported_forms
                passed = amount_ok and form_ok
                return (
                    "pass" if passed else "fail",
                    ["GUARANTEE_CAPACITY_SUFFICIENT" if passed else "GUARANTEE_CAPACITY_INSUFFICIENT"],
                    inputs,
                    _gate_summary(mode="guarantee_capacity_v1", compared=1, matched=int(passed), mismatched=int(not passed)),
                )
    elif gate_code == "HG06":
        tender = facts.get("tender.schedule.site_constraints")
        capacity = facts.get("enterprise.bid_preparation.capacity")
        if _fact_supported(tender) and _fact_supported(capacity):
            items = _structured_items(tender.value_json, "requirements", "constraints", "items")
            cap_value = capacity.value_json if isinstance(capacity.value_json, dict) else {}
            available = _decimal_value(cap_value.get("available_person_days"))
            required_values = [] if items is None else [
                _decimal_value(item.get("required_bid_person_days") or item.get("bid_preparation_person_days"))
                for item in items
            ]
            required_values = [value for value in required_values if value is not None]
            if available is not None and required_values:
                required = max(required_values)
                passed = available >= required
                return (
                    "pass" if passed else "fail",
                    ["BID_PREPARATION_CAPACITY_SUFFICIENT" if passed else "BID_PREPARATION_CAPACITY_INSUFFICIENT"],
                    [tender, capacity],
                    _gate_summary(mode="bid_preparation_capacity_v1", compared=1, matched=int(passed), mismatched=int(not passed)),
                )
    elif gate_code == "HG07":
        rules_fact = facts.get("enterprise.prohibited_risk.rules")
        risk_fact = facts.get("enterprise.client_risk.current_records")
        overview = facts.get("tender.overview")
        if _fact_supported(rules_fact) and _fact_supported(risk_fact):
            rules = _structured_items(rules_fact.value_json, "rules", "items")
            risks = _structured_items(risk_fact.value_json, "records", "risks", "items")
            if rules is not None and risks is not None:
                counterparties: set[str] = set()
                if _fact_supported(overview) and isinstance(overview.value_json, dict):
                    counterparties = {
                        token for token in (
                            _machine_token(overview.value_json.get(key))
                            for key in ("procurer_name", "owner_name", "client_name", "counterparty")
                        ) if token
                    }
                global_trigger = any(
                    item.get("triggered") is True and _machine_token(item.get("scope") or "global") == "global"
                    for item in rules
                )
                matched_risk = False
                for item in [*rules, *risks]:
                    counterparty = _machine_token(item.get("counterparty") or item.get("client_name"))
                    risk_status = _machine_token(item.get("risk_level") or item.get("status"))
                    active = item.get("active") is not False
                    if active and counterparty and counterparty in counterparties and risk_status in {
                        "blocked", "high", "prohibited", "禁入", "高风险",
                    }:
                        matched_risk = True
                        break
                inputs = [fact for fact in (rules_fact, risk_fact, overview) if fact is not None]
                if global_trigger or matched_risk:
                    return "fail", ["ENTERPRISE_PROHIBITED_RISK_TRIGGERED"], inputs, _gate_summary(mode="prohibited_risk_exact_counterparty_v1", compared=1, mismatched=1)
                if risks and not counterparties:
                    return "unknown", ["TENDER_COUNTERPARTY_NOT_MACHINE_COMPARABLE"], inputs, _gate_summary(mode="prohibited_risk_exact_counterparty_v1", indeterminate=1)
                return "pass", ["NO_APPLICABLE_ENTERPRISE_PROHIBITED_RISK"], inputs, _gate_summary(mode="prohibited_risk_exact_counterparty_v1", compared=1, matched=1)

    return _legacy_gate_result(gate_code, facts)


def evaluate_hard_gate(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    now: datetime | None = None,
) -> AuthorityOutput:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    _attempt, task, run = lock_task_claim(db, claim, now=current_time)
    definition = GATE_DEFINITIONS.get(str(task.task_type))
    if definition is None:
        raise BidMvp1AuthorityError("BID_HARD_GATE_TASK_INVALID")
    gate_code, severity = definition
    existing = (
        db.query(BidHardGateResult)
        .filter(BidHardGateResult.run_id == run.id, BidHardGateResult.gate_code == gate_code)
        .one_or_none()
    )
    if existing is not None:
        return _output("hard-gate", str(existing.id), dict(existing.details_json or {}))
    facts = _resolved_fact_map(db, str(run.id))
    status, reasons, input_facts, comparison = _gate_compare(gate_code, facts, run)
    unique_inputs = {str(fact.id): fact for fact in input_facts}
    details = {
        "authority_version": GATE_AUTHORITY_VERSION,
        "gate_code": gate_code,
        "status": status,
        "severity": severity,
        "input_fact_slots": sorted({str(fact.fact_slot) for fact in unique_inputs.values()}),
        "input_fact_ids": sorted(unique_inputs),
        "reason_codes": reasons,
        "comparison": comparison,
        "acceptance": _gate_acceptance_projection(
            gate_code,
            status=status,
            facts=facts,
        ),
    }
    gate = BidHardGateResult(
        id=str(uuid.uuid4()), run_id=str(run.id), task_id=str(task.id),
        gate_code=gate_code, status=status, severity=severity,
        reason_codes_json=reasons, input_fact_ids_json=details["input_fact_ids"],
        details_json=details, result_hash=canonical_hash(details), created_at=current_time,
    )
    db.add(gate)
    db.flush()
    return _output("hard-gate", str(gate.id), details)


def persist_claim_candidates(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    model_result_id: str,
    now: datetime | None = None,
) -> AuthorityOutput:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = lock_task_claim(db, claim, now=current_time)
    result, _observed_result_ids = _candidate_lineage(
        db,
        task=task,
        attempt_id=str(attempt.id),
        model_result_id=model_result_id,
        action_type="submit_claim_candidates",
    )
    candidates = list((result.action_json or {}).get("candidates") or [])
    if not candidates:
        raise BidMvp1AuthorityError("BID_CLAIM_CANDIDATES_EMPTY")
    next_order = int(
        db.query(func.max(BidReportClaim.claim_order))
        .filter(BidReportClaim.run_id == run.id)
        .scalar()
        or 0
    )
    claim_ids: list[str] = []
    for candidate in candidates:
        support_ids = sorted(set(str(value) for value in candidate.get("support_ids") or []))
        facts = db.query(BidResolvedFact).filter(
            BidResolvedFact.run_id == run.id,
            BidResolvedFact.id.in_(tuple(support_ids)) if support_ids else False,
        ).all()
        gates = db.query(BidHardGateResult).filter(
            BidHardGateResult.run_id == run.id,
            BidHardGateResult.id.in_(tuple(support_ids)) if support_ids else False,
        ).all()
        known = {str(row.id) for row in facts} | {str(row.id) for row in gates}
        if not support_ids or known != set(support_ids):
            raise BidMvp1AuthorityError("BID_CLAIM_SUPPORT_OUT_OF_SCOPE")
        claim_payload = {
            "run_id": str(run.id),
            "claim_type": str(candidate.get("claim_type")),
            "text": str(candidate.get("text") or "").strip(),
            "support_fact_ids": sorted(str(row.id) for row in facts),
            "support_gate_ids": sorted(str(row.id) for row in gates),
            "premise_or_trigger": candidate.get("premise_or_trigger"),
        }
        claim_hash = canonical_hash(claim_payload)
        row = (
            db.query(BidReportClaim)
            .filter(BidReportClaim.run_id == run.id, BidReportClaim.claim_hash == claim_hash)
            .one_or_none()
        )
        if row is None:
            next_order += 1
            row = BidReportClaim(
                id=str(uuid.uuid4()), run_id=str(run.id), task_id=str(task.id),
                model_result_id=str(result.id), claim_order=next_order,
                claim_type=claim_payload["claim_type"], text=claim_payload["text"],
                status="candidate", support_fact_ids_json=claim_payload["support_fact_ids"],
                support_gate_ids_json=claim_payload["support_gate_ids"],
                premise_or_trigger=claim_payload["premise_or_trigger"],
                reason_codes_json=["MODEL_CANDIDATE_AWAITING_DETERMINISTIC_VALIDATION"],
                claim_hash=claim_hash, created_at=current_time,
            )
            db.add(row)
            db.flush()
            assertion_ids = {
                str(assertion_id)
                for fact in facts
                for assertion_id in (fact.source_assertion_ids_json or [])
            }
            links = db.query(BidFactEvidenceLink).filter(
                BidFactEvidenceLink.assertion_id.in_(tuple(assertion_ids))
                if assertion_ids else False
            ).all()
            fragments = _scoped_evidence(
                db, run=run,
                evidence_ids=[str(link.evidence_fragment_id) for link in links],
            )
            linked_fragment_ids: set[str] = set()
            for link in links:
                fragment = fragments[str(link.evidence_fragment_id)]
                if str(fragment.id) in linked_fragment_ids:
                    continue
                linked_fragment_ids.add(str(fragment.id))
                excerpt = str(fragment.normalized_text)[:900]
                excerpt_hash = canonical_hash(excerpt)
                citation_payload = {
                    "claim_id": str(row.id), "assertion_id": str(link.assertion_id),
                    "evidence_id": str(fragment.id), "excerpt_hash": excerpt_hash,
                    "locator_hash": str(fragment.locator_hash),
                }
                db.add(BidClaimCitation(
                    id=str(uuid.uuid4()), claim_id=str(row.id), assertion_id=str(link.assertion_id),
                    evidence_fragment_id=str(fragment.id), document_version_id=str(fragment.document_version_id),
                    locator_json=dict(fragment.locator_json or {}), excerpt=excerpt,
                    excerpt_hash=excerpt_hash, citation_hash=canonical_hash(citation_payload),
                    created_at=current_time,
                ))
        claim_ids.append(str(row.id))
    db.flush()
    return _output(
        "report-claims", str(result.id),
        {"run_id": str(run.id), "model_result_id": str(result.id), "claim_ids": claim_ids},
    )


def evaluate_preliminary_decision(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    now: datetime | None = None,
) -> AuthorityOutput:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    _attempt, task, run = lock_task_claim(db, claim, now=current_time)
    existing = db.query(BidPreliminaryDecision).filter(BidPreliminaryDecision.run_id == run.id).one_or_none()
    if existing is not None:
        return _output("preliminary-decision", str(existing.id), {"decision": existing.decision, "decision_hash": existing.decision_hash})
    gates = db.query(BidHardGateResult).filter(BidHardGateResult.run_id == run.id).order_by(BidHardGateResult.gate_code).all()
    if len(gates) != 7:
        raise BidMvp1AuthorityError("BID_DECISION_GATE_SET_INCOMPLETE")
    failed = sum(str(row.status) == "fail" and str(row.severity) == "block" for row in gates)
    unknown = sum(str(row.status) == "unknown" for row in gates)
    unknown_facts = int(db.query(func.count(BidFactCoverage.id)).filter(
        BidFactCoverage.run_id == run.id,
        BidFactCoverage.status.in_(("not_assessed", "missing", "conflicted", "unavailable", "blocked_by_parent")),
    ).scalar() or 0)
    if failed:
        decision, investment, summary, reasons = "no_bid", "hold", "存在阻断性硬门禁，当前不建议投入投标。", ["BLOCKING_GATE_FAILED"]
    elif unknown:
        decision, investment, summary, reasons = "insufficient", "hold", "关键企业能力或合规事实尚未形成权威证据，暂不作投标承诺。", ["HARD_GATE_INPUT_INSUFFICIENT"]
    else:
        decision, investment, summary, reasons = "conditional", "medium", "硬门禁已通过，可在补齐非阻断未知项后有条件推进。", ["ALL_HARD_GATES_PASSED"]
    inputs = {"gate_hashes": [str(row.result_hash) for row in gates], "unknown_fact_count": unknown_facts, "rule_set_id": str(run.rule_set_id), "formula_catalog_version_id": str(run.formula_catalog_version_id)}
    input_hash = canonical_hash(inputs)
    decision_payload = {"authority_version": DECISION_AUTHORITY_VERSION, "run_id": str(run.id), "decision": decision, "investment_level": investment, "failed_gate_count": failed, "unknown_gate_count": unknown, "unknown_fact_count": unknown_facts, "summary": summary, "reason_codes": reasons, "input_hash": input_hash}
    row = BidPreliminaryDecision(
        id=str(uuid.uuid4()), run_id=str(run.id), task_id=str(task.id),
        rule_set_id=str(run.rule_set_id), formula_catalog_version_id=str(run.formula_catalog_version_id),
        decision=decision, investment_level=investment, failed_gate_count=failed,
        unknown_gate_count=unknown, unknown_fact_count=unknown_facts, summary=summary,
        reason_codes_json=reasons, input_hash=input_hash,
        decision_hash=canonical_hash(decision_payload), created_at=current_time,
    )
    db.add(row)
    db.flush()
    return _output("preliminary-decision", str(row.id), decision_payload)


def validate_claims(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    final_consistency: bool,
    now: datetime | None = None,
) -> AuthorityOutput:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    _attempt, task, run = lock_task_claim(db, claim, now=current_time)
    claims = db.query(BidReportClaim).filter(BidReportClaim.run_id == run.id).order_by(BidReportClaim.claim_order).all()
    checks = []
    for row in claims:
        support_count = len(row.support_fact_ids_json or []) + len(row.support_gate_ids_json or [])
        citation_count = int(db.query(func.count(BidClaimCitation.id)).filter(BidClaimCitation.claim_id == row.id).scalar() or 0)
        supported_facts = db.query(BidResolvedFact).filter(
            BidResolvedFact.run_id == run.id,
            BidResolvedFact.id.in_(tuple(row.support_fact_ids_json or []))
            if row.support_fact_ids_json else False,
        ).all()
        assertion_ids = {
            str(assertion_id)
            for fact in supported_facts
            for assertion_id in (fact.source_assertion_ids_json or [])
        }
        enterprise_lineage_count = int(
            db.query(func.count(BidFactEnterpriseLink.assertion_id)).filter(
                BidFactEnterpriseLink.assertion_id.in_(tuple(assertion_ids))
                if assertion_ids else False
            ).scalar()
            or 0
        )
        comparison_lineage_count = int(
            db.query(func.count(BidFactComparisonLink.assertion_id)).filter(
                BidFactComparisonLink.assertion_id.in_(tuple(assertion_ids))
                if assertion_ids else False
            ).scalar()
            or 0
        )
        valid = support_count > 0 and (
            str(row.claim_type) in {"inference", "recommendation"}
            or citation_count > 0
            or enterprise_lineage_count > 0
            or comparison_lineage_count > 0
            or bool(row.support_gate_ids_json)
        )
        row.status = "valid" if valid else "invalid"
        row.reason_codes_json = ["CLAIM_SUPPORT_VALIDATED"] if valid else ["CLAIM_DIRECT_SUPPORT_MISSING"]
        checks.append({
            "claim_id": str(row.id),
            "valid": valid,
            "support_count": support_count,
            "citation_count": citation_count,
            "enterprise_lineage_count": enterprise_lineage_count,
            "comparison_lineage_count": comparison_lineage_count,
        })
    if not final_consistency:
        db.flush()
        return _output("claim-validation", str(run.id), {"run_id": str(run.id), "checks": checks, "passed": all(item["valid"] for item in checks) and bool(checks)})
    decision = db.query(BidPreliminaryDecision).filter(BidPreliminaryDecision.run_id == run.id).one_or_none()
    gates = db.query(BidHardGateResult).filter(BidHardGateResult.run_id == run.id).all()
    passed = bool(claims) and all(item["valid"] for item in checks) and decision is not None and len(gates) == 7
    validation_payload = {"validator_version": CLAIM_VALIDATOR_VERSION, "run_id": str(run.id), "checks": checks, "decision_present": decision is not None, "gate_count": len(gates), "passed": passed}
    input_hash = canonical_hash({"claim_hashes": [str(row.claim_hash) for row in claims], "decision_hash": str(decision.decision_hash) if decision else None, "gate_hashes": sorted(str(row.result_hash) for row in gates)})
    existing = db.query(BidReportValidation).filter(BidReportValidation.run_id == run.id).one_or_none()
    if existing is None:
        existing = BidReportValidation(
            id=str(uuid.uuid4()), run_id=str(run.id), task_id=str(task.id),
            status="passed" if passed else "failed", validator_version=CLAIM_VALIDATOR_VERSION,
            checks_json=validation_payload, input_hash=input_hash,
            result_hash=canonical_hash(validation_payload), created_at=current_time,
        )
        db.add(existing)
    db.flush()
    return _output("report-validation", str(existing.id), validation_payload)


def generate_preliminary_report(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    now: datetime | None = None,
) -> AuthorityOutput:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    _attempt, task, run = lock_task_claim(db, claim, now=current_time)
    existing = db.query(BidPreliminaryReport).filter(BidPreliminaryReport.run_id == run.id).one_or_none()
    if existing is not None:
        return _output("preliminary-report", str(existing.id), dict(existing.report_json or {}))
    validation = db.query(BidReportValidation).filter(BidReportValidation.run_id == run.id, BidReportValidation.status == "passed").one_or_none()
    decision = db.query(BidPreliminaryDecision).filter(BidPreliminaryDecision.run_id == run.id).one_or_none()
    if validation is None or decision is None:
        raise BidMvp1AuthorityError("BID_REPORT_VALIDATION_NOT_PASSED")
    # Lock the Assessment while allocating its monotonically increasing
    # report version.  Without this, two distinct Runs for one Assessment can
    # race on MAX(version)+1 and turn a recoverable scheduling overlap into a
    # uniqueness failure.
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == run.assessment_id)
        .with_for_update()
        .one()
    )
    existing = (
        db.query(BidPreliminaryReport)
        .filter(BidPreliminaryReport.run_id == run.id)
        .one_or_none()
    )
    if existing is not None:
        return _output("preliminary-report", str(existing.id), dict(existing.report_json or {}))
    gates = db.query(BidHardGateResult).filter(BidHardGateResult.run_id == run.id).order_by(BidHardGateResult.gate_code).all()
    facts = db.query(BidResolvedFact).join(BidResolvedFactHead, BidResolvedFactHead.resolved_fact_id == BidResolvedFact.id).filter(BidResolvedFactHead.run_id == run.id).order_by(BidResolvedFact.fact_slot).all()
    claims = db.query(BidReportClaim).filter(BidReportClaim.run_id == run.id, BidReportClaim.status == "valid").order_by(BidReportClaim.claim_order).all()
    claim_payload = []
    for row in claims:
        citations = db.query(BidClaimCitation).filter(BidClaimCitation.claim_id == row.id).order_by(BidClaimCitation.id).all()
        claim_payload.append({"claim_id": str(row.id), "claim_type": str(row.claim_type), "text": str(row.text), "premise_or_trigger": row.premise_or_trigger, "citations": [{"evidence_id": str(item.evidence_fragment_id), "document_version_id": str(item.document_version_id), "locator": dict(item.locator_json or {}), "excerpt": str(item.excerpt)} for item in citations]})
    report_json = {
        "schema": "bid.preliminary.report.mvp1.v1",
        "renderer_version": REPORT_RENDERER_VERSION,
        "assessment_id": str(run.assessment_id), "run_id": str(run.id),
        "title": str(assessment.title),
        "decision": {"code": str(decision.decision), "investment_level": str(decision.investment_level), "summary": str(decision.summary), "reason_codes": list(decision.reason_codes_json or [])},
        "hard_gates": [
            {
                "gate_code": str(row.gate_code),
                "status": str(row.status),
                "severity": str(row.severity),
                "reason_codes": list(row.reason_codes_json or []),
                "input_fact_slots": list((row.details_json or {}).get("input_fact_slots") or []),
                "comparison": dict((row.details_json or {}).get("comparison") or {}),
                "acceptance": dict((row.details_json or {}).get("acceptance") or {}),
            }
            for row in gates
        ],
        "facts": [{"fact_id": str(row.id), "fact_slot": str(row.fact_slot), "status": str(row.status), "value_type": row.value_type, "value": row.value_json, "reason_codes": list(row.reason_codes_json or [])} for row in facts],
        "claims": claim_payload,
        "limitations": ["本报告仅使用当前 Run 冻结 Manifest、当前 ParseHead、冻结企业能力快照、冻结配置和已验证证据。", "缺失、部分、冲突或不可机器比较的事实保持 unknown，不从文件名、MIME、parser_hint 或自由文本关键词推断硬门结论。"],
        "generated_at": current_time.isoformat().replace("+00:00", "Z"),
    }
    report_hash = canonical_hash(report_json)
    version = int(db.query(func.max(BidPreliminaryReport.report_version)).filter(BidPreliminaryReport.assessment_id == run.assessment_id).scalar() or 0) + 1
    row = BidPreliminaryReport(
        id=str(uuid.uuid4()), assessment_id=str(run.assessment_id), run_id=str(run.id),
        decision_id=str(decision.id), validation_id=str(validation.id), report_version=version,
        status="ready", title=f"{assessment.title} · 投标机会初筛报告",
        executive_summary=str(decision.summary), report_json=report_json,
        report_hash=report_hash, generated_at=current_time, created_at=current_time,
    )
    db.add(row)
    db.flush()
    event = append_outbox_event(
        db,
        event_type="bid.report.published.v1",
        producer="bid-mvp1-report-authority-v1",
        aggregate_type="report",
        aggregate_id=str(row.id),
        aggregate_version=int(row.report_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=f"mvp1-report:{row.id}",
        payload_schema="bid.report.published.v1.payload",
        payload={
            "report_id": str(row.id),
            "report_type": "preliminary",
            "version": int(row.report_version),
            "decision_class": str(decision.decision),
            "run_id": str(run.id),
            "report_hash": report_hash,
        },
        dedupe_key=f"preliminary-report-published:{row.id}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref="service:bid-mvp1-report-authority",
        action="preliminary_report.publish",
        entity_type="report",
        entity_id=str(row.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=f"mvp1-report:{row.id}",
        correlation_id=str(event.event_id),
        after={
            "run_id": str(run.id),
            "report_version": int(row.report_version),
            "status": str(row.status),
            "report_hash": report_hash,
        },
        occurred_at=current_time,
    )
    return _output("preliminary-report", str(row.id), report_json)
