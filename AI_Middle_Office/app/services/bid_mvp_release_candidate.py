"""Phase 4C-3 deterministic business acceptance and MVP release freeze."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
)
from app.models.bid_assessment_config import BidEnterpriseSnapshot
from app.models.bid_assessment_documents import BidEvidenceFragment
from app.models.bid_assessment_release import (
    BidEnterpriseBusinessBaseline,
    BidMvpReleaseCandidate,
)
from app.models.bid_assessment_results import (
    BidClaimCitation,
    BidHardGateResult,
    BidPreliminaryDecision,
    BidPreliminaryReport,
    BidReportClaim,
    BidReportValidation,
)
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.models.bid_run_validation import BidRunValidation
from app.services.bid_assessment_eventing import (
    append_audit_log,
    as_utc,
    canonical_hash,
)


MVP_RC_SCHEMA = "bid.mvp.release-candidate.v1"
MVP_RC_VALIDATION_SCHEMA = "bid.mvp.release-candidate-validation.v1"
MVP_RC_AUTHORITY_VERSION = "bid-mvp-release-candidate-authority-v1"
MVP_RC_GATE_CODES = tuple(f"HG{index:02d}" for index in range(1, 8))
MVP_RC_QUALITY_CODES = (
    "REPORT_BUSINESS_READABLE",
    "CITATIONS_TRACEABLE",
    "UNKNOWNS_EXPLICIT",
    "DECISION_REASONABLE",
    "PARSE_LIMITATIONS_REVIEWED",
)
_DECISION_AUTHORITY_VERSION = "bid-preliminary-decision-mvp1-v1"
_RELEASE_NAMESPACE = uuid.UUID("f091ea55-2324-49d8-a419-31c0f43883e1")


class BidMvpReleaseCandidateError(RuntimeError):
    code = "BID_MVP_RELEASE_CANDIDATE_ERROR"


@dataclass(frozen=True)
class FrozenMvpReleaseCandidateResult:
    release: BidMvpReleaseCandidate
    created: bool
    projection: dict[str, Any]


def _database_utc_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "mysql":
        value = db.execute(select(func.utc_timestamp(6))).scalar_one()
    else:
        value = db.execute(select(func.current_timestamp())).scalar_one()
    if not isinstance(value, datetime):
        raise BidMvpReleaseCandidateError("BID_DATABASE_TIME_INVALID")
    return as_utc(value)


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _check(
    rows: list[dict[str, Any]],
    code: str,
    passed: bool,
    *,
    label: str,
    warning: bool = False,
    detail: Any = None,
) -> None:
    rows.append(
        {
            "code": code,
            "label": label,
            "status": "passed" if passed else ("warning" if warning else "blocked"),
            "detail": detail,
        }
    )


def _normalized_reviews(command: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gate_reviews = sorted(
        (
            {
                "gate_code": str(item["gate_code"]),
                "disposition": str(item["disposition"]),
                "note": str(item.get("note") or "").strip() or None,
            }
            for item in command.get("gate_reviews") or []
        ),
        key=lambda item: item["gate_code"],
    )
    quality_reviews = sorted(
        (
            {
                "code": str(item["code"]),
                "disposition": str(item["disposition"]),
                "note": str(item.get("note") or "").strip() or None,
            }
            for item in command.get("quality_reviews") or []
        ),
        key=lambda item: item["code"],
    )
    if [item["gate_code"] for item in gate_reviews] != list(MVP_RC_GATE_CODES):
        raise BidMvpReleaseCandidateError("BID_MVP_RC_GATE_REVIEW_SET_INVALID")
    if [item["code"] for item in quality_reviews] != sorted(MVP_RC_QUALITY_CODES):
        raise BidMvpReleaseCandidateError("BID_MVP_RC_QUALITY_REVIEW_SET_INVALID")
    if any(
        item["disposition"] not in {"confirmed", "correction_required", "not_reviewed"}
        for item in gate_reviews + quality_reviews
    ):
        raise BidMvpReleaseCandidateError("BID_MVP_RC_REVIEW_DISPOSITION_INVALID")
    return gate_reviews, quality_reviews


def _release_projection(row: BidMvpReleaseCandidate) -> dict[str, Any]:
    return {
        "schema": MVP_RC_SCHEMA,
        "release_candidate_id": str(row.id),
        "version": str(row.version),
        "status": str(row.status),
        "acceptance_outcome": str(row.acceptance_outcome),
        "assessment_id": str(row.assessment_id),
        "run_id": str(row.run_id),
        "report_id": str(row.report_id),
        "run_validation_id": str(row.run_validation_id),
        "enterprise_snapshot_id": str(row.enterprise_snapshot_id),
        "reviewer_id": int(row.reviewer_id),
        "review_note": str(row.review_note),
        "review": dict(row.review_json or {}),
        "source_hashes": dict(row.source_hashes_json or {}),
        "manifest": dict(row.manifest_json or {}),
        "candidate_hash": str(row.candidate_hash),
        "release_hash": str(row.release_hash),
        "reviewed_at": _utc_text(row.reviewed_at),
    }


def _run_validation_result_hash_valid(
    result_json: dict[str, Any] | None,
    expected_hash: str | None,
) -> bool:
    """Verify both historical and self-describing Run Validation results."""

    payload = dict(result_json or {})
    normalized_expected = str(expected_hash or "")
    embedded_hash = payload.pop("result_hash", None)
    if embedded_hash is not None and str(embedded_hash) != normalized_expected:
        return False
    return bool(normalized_expected) and canonical_hash(payload) == normalized_expected


def get_mvp_release_candidate(
    db: Session,
    *,
    run_id: str,
) -> dict[str, Any] | None:
    row = (
        db.query(BidMvpReleaseCandidate)
        .filter(BidMvpReleaseCandidate.run_id == run_id)
        .one_or_none()
    )
    return None if row is None else _release_projection(row)


def preview_mvp_release_candidate(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
) -> dict[str, Any]:
    run_id = str(command.get("run_id") or "")
    review_note = str(command.get("review_note") or "").strip()
    if not run_id or not review_note:
        raise BidMvpReleaseCandidateError("BID_MVP_RC_COMMAND_INVALID")
    gate_reviews, quality_reviews = _normalized_reviews(command)

    run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == run_id).one_or_none()
    if run is None:
        raise BidMvpReleaseCandidateError("BID_MVP_RC_RUN_NOT_FOUND")
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == run.assessment_id)
        .one()
    )
    report = (
        db.query(BidPreliminaryReport)
        .filter(BidPreliminaryReport.run_id == run.id)
        .one_or_none()
    )
    report_validation = (
        db.query(BidReportValidation)
        .filter(BidReportValidation.id == report.validation_id)
        .one_or_none()
        if report is not None
        else None
    )
    run_validation = (
        db.query(BidRunValidation)
        .filter(BidRunValidation.run_id == run.id)
        .one_or_none()
    )
    snapshot = (
        db.query(BidEnterpriseSnapshot)
        .filter(BidEnterpriseSnapshot.id == run.enterprise_snapshot_id)
        .one_or_none()
    )
    manifest = (
        db.query(BidDocumentManifest)
        .filter(BidDocumentManifest.id == run.manifest_id)
        .one_or_none()
    )
    scope = (
        db.query(BidAssessmentScope)
        .filter(BidAssessmentScope.id == run.scope_id)
        .one_or_none()
    )
    latest_scope = (
        db.query(BidAssessmentScope)
        .filter(BidAssessmentScope.assessment_id == run.assessment_id)
        .order_by(BidAssessmentScope.version.desc(), BidAssessmentScope.id.desc())
        .first()
    )
    business_baseline = None
    if getattr(settings, "feature_bid_assessment_phase4_business_baseline", False):
        business_baseline = (
            db.query(BidEnterpriseBusinessBaseline)
            .filter(
                BidEnterpriseBusinessBaseline.snapshot_id == run.enterprise_snapshot_id,
                BidEnterpriseBusinessBaseline.status == "frozen",
            )
            .one_or_none()
        )
        latest_business_baseline = (
            db.query(BidEnterpriseBusinessBaseline)
            .filter(BidEnterpriseBusinessBaseline.status == "frozen")
            .order_by(
                BidEnterpriseBusinessBaseline.reviewed_at.desc(),
                BidEnterpriseBusinessBaseline.id.desc(),
            )
            .first()
        )
        latest_snapshot = (
            db.query(BidEnterpriseSnapshot)
            .filter(
                BidEnterpriseSnapshot.id == latest_business_baseline.snapshot_id
            )
            .one_or_none()
            if latest_business_baseline is not None
            else None
        )
    else:
        latest_snapshot = (
            db.query(BidEnterpriseSnapshot)
            .filter(BidEnterpriseSnapshot.status == "frozen")
            .order_by(
                BidEnterpriseSnapshot.as_of.desc(),
                BidEnterpriseSnapshot.frozen_at.desc(),
                BidEnterpriseSnapshot.id.desc(),
            )
            .first()
        )
    gates = (
        db.query(BidHardGateResult)
        .filter(BidHardGateResult.run_id == run.id)
        .order_by(BidHardGateResult.gate_code.asc())
        .all()
    )
    decision = (
        db.query(BidPreliminaryDecision)
        .filter(BidPreliminaryDecision.run_id == run.id)
        .one_or_none()
    )
    source_release: BidMvpReleaseCandidate | None = None
    source_decision: BidPreliminaryDecision | None = None
    source_gate_map: dict[str, BidHardGateResult] = {}
    if getattr(settings, "feature_bid_assessment_phase4_business_baseline", False):
        requested_source_release_id = str(
            command.get("source_release_candidate_id") or ""
        ).strip()
        source_query = db.query(BidMvpReleaseCandidate).filter(
            BidMvpReleaseCandidate.assessment_id == run.assessment_id,
            BidMvpReleaseCandidate.run_id != run.id,
            BidMvpReleaseCandidate.status == "frozen",
        )
        if requested_source_release_id:
            source_query = source_query.filter(
                BidMvpReleaseCandidate.id == requested_source_release_id
            )
        source_release = source_query.order_by(
            BidMvpReleaseCandidate.reviewed_at.desc(),
            BidMvpReleaseCandidate.id.desc(),
        ).first()
        if source_release is not None:
            source_decision = (
                db.query(BidPreliminaryDecision)
                .filter(BidPreliminaryDecision.run_id == source_release.run_id)
                .one_or_none()
            )
            source_gate_map = {
                str(row.gate_code): row
                for row in db.query(BidHardGateResult)
                .filter(BidHardGateResult.run_id == source_release.run_id)
                .all()
            }
    claim_rows = (
        db.query(BidReportClaim)
        .filter(BidReportClaim.run_id == run.id)
        .order_by(BidReportClaim.claim_order.asc(), BidReportClaim.id.asc())
        .all()
    )
    valid_claim_count = sum(1 for row in claim_rows if str(row.status) == "valid")
    citation_rows = (
        db.query(BidClaimCitation, BidEvidenceFragment)
        .join(BidReportClaim, BidReportClaim.id == BidClaimCitation.claim_id)
        .join(BidEvidenceFragment, BidEvidenceFragment.id == BidClaimCitation.evidence_fragment_id)
        .filter(BidReportClaim.run_id == run.id)
        .all()
    )
    atom_violations = sum(
        1
        for _citation, fragment in citation_rows
        if str((fragment.locator_json or {}).get("fragment_role") or "")
        != "evidence_atom"
        or (fragment.locator_json or {}).get("is_citable") is not True
    )

    checks: list[dict[str, Any]] = []
    _check(
        checks,
        "RUN_SUCCEEDED",
        str(run.status) == "succeeded",
        label="Run 已成功收敛",
        detail=str(run.status),
    )
    _check(
        checks,
        "RUN_IS_CURRENT",
        str(assessment.active_run_id or "") == str(run.id)
        and str(assessment.lifecycle_status) == "active",
        label="Run 仍是当前有效版本",
    )
    _check(
        checks,
        "MANIFEST_IS_CURRENT",
        manifest is not None
        and str(assessment.current_manifest_id or "") == str(run.manifest_id),
        label="资料 Manifest 未变化",
    )
    _check(
        checks,
        "SCOPE_IS_CURRENT",
        scope is not None
        and latest_scope is not None
        and str(latest_scope.id) == str(run.scope_id),
        label="标段 Scope 未变化",
    )
    _check(
        checks,
        "ENTERPRISE_SNAPSHOT_IS_CURRENT",
        snapshot is not None
        and str(snapshot.status) == "frozen"
        and bool(snapshot.snapshot_hash)
        and latest_snapshot is not None
        and str(latest_snapshot.id) == str(run.enterprise_snapshot_id),
        label="企业能力基线仍是最新冻结版本",
    )
    _check(
        checks,
        "RUN_VALIDATION_PASSED",
        run_validation is not None
        and str(run_validation.status) == "passed"
        and str(run_validation.outcome) == "passed"
        and _run_validation_result_hash_valid(
            dict(run_validation.result_json or {}),
            str(run_validation.result_hash or ""),
        ),
        label="Run Validation 通过且 Hash 一致",
    )
    report_hash_valid = bool(
        report is not None
        and str(report.status) == "ready"
        and str(report.report_hash) == canonical_hash(dict(report.report_json or {}))
    )
    _check(
        checks,
        "REPORT_READY_AND_HASHED",
        report_hash_valid,
        label="报告已就绪且 Hash 一致",
    )
    report_validation_input_hash = canonical_hash(
        {
            "claim_hashes": [str(row.claim_hash) for row in claim_rows],
            "decision_hash": str(decision.decision_hash) if decision is not None else None,
            "gate_hashes": sorted(str(row.result_hash) for row in gates),
        }
    )
    report_validation_hash_valid = bool(
        report_validation is not None
        and str(report_validation.status) == "passed"
        and str(report_validation.result_hash)
        == canonical_hash(dict(report_validation.checks_json or {}))
        and str(report_validation.input_hash) == report_validation_input_hash
    )
    _check(
        checks,
        "REPORT_VALIDATION_PASSED",
        report_validation_hash_valid,
        label="报告事实与引用校验通过且 Hash 一致",
    )

    report_gates = {
        str(item.get("gate_code") or ""): item
        for item in ((report.report_json or {}).get("hard_gates") or [])
        if isinstance(item, dict)
    } if report is not None else {}
    gate_map = {str(row.gate_code): row for row in gates}
    gates_complete = set(gate_map) == set(MVP_RC_GATE_CODES)
    gate_hashes_valid = gates_complete and all(
        str(gate_map[code].result_hash)
        == canonical_hash(dict(gate_map[code].details_json or {}))
        for code in MVP_RC_GATE_CODES
    )
    _check(
        checks,
        "HARD_GATE_HASHES_VALID",
        gate_hashes_valid,
        label="HG01—HG07 权威结果 Hash 一致",
    )
    report_gate_consistent = (
        gates_complete
        and set(report_gates) == set(MVP_RC_GATE_CODES)
        and all(
            str(report_gates[code].get("status") or "") == str(gate_map[code].status)
            and str(report_gates[code].get("severity") or "")
            == str(gate_map[code].severity)
            and list(report_gates[code].get("reason_codes") or [])
            == list(gate_map[code].reason_codes_json or [])
            and list(report_gates[code].get("input_fact_slots") or [])
            == list((gate_map[code].details_json or {}).get("input_fact_slots") or [])
            and dict(report_gates[code].get("comparison") or {})
            == dict((gate_map[code].details_json or {}).get("comparison") or {})
            and dict(report_gates[code].get("acceptance") or {})
            == dict((gate_map[code].details_json or {}).get("acceptance") or {})
            for code in MVP_RC_GATE_CODES
        )
    )
    _check(
        checks,
        "HARD_GATES_COMPLETE",
        gates_complete,
        label="HG01—HG07 权威结果完整",
        detail=len(gates),
    )
    _check(
        checks,
        "HARD_GATE_REPORT_CONSISTENT",
        report_gate_consistent,
        label="报告硬门结果及解释与权威一致",
    )
    decision_input_hash = canonical_hash(
        {
            "gate_hashes": [str(row.result_hash) for row in gates],
            "unknown_fact_count": int(decision.unknown_fact_count) if decision is not None else None,
            "rule_set_id": str(run.rule_set_id),
            "formula_catalog_version_id": str(run.formula_catalog_version_id),
        }
    )
    decision_payload = (
        {
            "authority_version": _DECISION_AUTHORITY_VERSION,
            "run_id": str(run.id),
            "decision": str(decision.decision),
            "investment_level": str(decision.investment_level),
            "failed_gate_count": int(decision.failed_gate_count),
            "unknown_gate_count": int(decision.unknown_gate_count),
            "unknown_fact_count": int(decision.unknown_fact_count),
            "summary": str(decision.summary),
            "reason_codes": list(decision.reason_codes_json or []),
            "input_hash": str(decision.input_hash),
        }
        if decision is not None
        else None
    )
    decision_consistent = bool(
        decision is not None
        and str(decision.input_hash) == decision_input_hash
        and str(decision.decision_hash) == canonical_hash(decision_payload)
        and report is not None
        and str(((report.report_json or {}).get("decision") or {}).get("code") or "")
        == str(decision.decision)
    )
    _check(
        checks,
        "DECISION_REPORT_CONSISTENT",
        decision_consistent,
        label="报告决策与确定性决策权威一致",
    )
    _check(
        checks,
        "CITATIONS_PRESENT",
        valid_claim_count > 0 and len(citation_rows) > 0,
        label="报告包含可追溯 Claim 与原文引用",
        detail={"valid_claim_count": valid_claim_count, "citation_count": len(citation_rows)},
    )
    _check(
        checks,
        "CITATIONS_ATOM_ONLY",
        atom_violations == 0,
        label="全部引用均指向可引用 Atom",
        detail={"violation_count": atom_violations},
    )
    if getattr(settings, "feature_bid_assessment_phase4_business_baseline", False):
        _check(
            checks,
            "ENTERPRISE_BUSINESS_BASELINE_VERIFIED",
            business_baseline is not None
            and str(business_baseline.status) == "frozen"
            and bool(business_baseline.baseline_hash)
            and business_baseline.reviewed_at is not None
            and run.evaluation_time is not None
            and as_utc(business_baseline.reviewed_at) <= as_utc(run.evaluation_time),
            label="Run 使用已核验的真实企业能力基线",
            detail=(
                str(business_baseline.verification_outcome)
                if business_baseline is not None
                else None
            ),
        )
        _check(
            checks,
            "DECISION_REVALIDATION_SOURCE_BOUND",
            source_release is not None
            and str(source_release.assessment_id) == str(run.assessment_id)
            and str(source_release.run_id) != str(run.id)
            and source_decision is not None
            and set(source_gate_map) == set(MVP_RC_GATE_CODES),
            label="业务决策复验已绑定同一研判的历史 RC",
            detail=(str(source_release.id) if source_release is not None else None),
        )

    reviewed_gates: list[dict[str, Any]] = []
    review_blocking_codes: list[str] = []
    for review in gate_reviews:
        gate = gate_map.get(review["gate_code"])
        gate_status = str(gate.status) if gate is not None else "missing"
        review_ready = review["disposition"] == "confirmed"
        reason_codes: list[str] = []
        if review["disposition"] == "not_reviewed":
            reason_codes.append("BUSINESS_REVIEW_REQUIRED")
        elif review["disposition"] == "correction_required":
            reason_codes.append("BUSINESS_CORRECTION_REQUIRED")
        if gate_status in {"fail", "unknown"} and not review["note"]:
            review_ready = False
            reason_codes.append("NONPASS_GATE_REVIEW_NOTE_REQUIRED")
        if not review_ready:
            review_blocking_codes.append(f"{review['gate_code']}_REVIEW_NOT_ACCEPTED")
        reviewed_gates.append(
            {
                **review,
                "gate_status": gate_status,
                "gate_result_hash": str(gate.result_hash) if gate is not None else None,
                "review_ready": review_ready,
                "reason_codes": reason_codes or ["BUSINESS_REVIEW_CONFIRMED"],
            }
        )

    reviewed_quality = [
        {
            **review,
            "review_ready": review["disposition"] == "confirmed",
            "reason_codes": (
                ["BUSINESS_REVIEW_CONFIRMED"]
                if review["disposition"] == "confirmed"
                else [
                    "BUSINESS_CORRECTION_REQUIRED"
                    if review["disposition"] == "correction_required"
                    else "BUSINESS_REVIEW_REQUIRED"
                ]
            ),
        }
        for review in quality_reviews
    ]
    review_blocking_codes.extend(
        f"{item['code']}_NOT_ACCEPTED"
        for item in reviewed_quality
        if not item["review_ready"]
    )

    blocking_codes = [item["code"] for item in checks if item["status"] == "blocked"]
    gate_outcomes = {
        code: str(gate_map[code].status) if code in gate_map else "missing"
        for code in MVP_RC_GATE_CODES
    }
    follow_up_required = any(
        status in {"fail", "unknown"} for status in gate_outcomes.values()
    )
    acceptance_outcome = (
        "accepted_with_follow_up" if follow_up_required else "accepted"
    )
    revalidation = None
    if getattr(settings, "feature_bid_assessment_phase4_business_baseline", False):
        revalidation = {
            "source_release_candidate_id": (
                str(source_release.id) if source_release is not None else None
            ),
            "source_release_hash": (
                str(source_release.release_hash) if source_release is not None else None
            ),
            "source_run_id": (
                str(source_release.run_id) if source_release is not None else None
            ),
            "target_run_id": str(run.id),
            "source_decision": (
                str(source_decision.decision) if source_decision is not None else None
            ),
            "target_decision": str(decision.decision) if decision is not None else None,
            "decision_changed": bool(
                source_decision is not None
                and decision is not None
                and str(source_decision.decision) != str(decision.decision)
            ),
            "gate_deltas": [
                {
                    "gate_code": code,
                    "source_status": (
                        str(source_gate_map[code].status)
                        if code in source_gate_map
                        else "missing"
                    ),
                    "target_status": (
                        str(gate_map[code].status) if code in gate_map else "missing"
                    ),
                    "changed": bool(
                        code not in source_gate_map
                        or code not in gate_map
                        or str(source_gate_map[code].status)
                        != str(gate_map[code].status)
                    ),
                }
                for code in MVP_RC_GATE_CODES
            ],
            "business_baseline_id": (
                str(business_baseline.id) if business_baseline is not None else None
            ),
            "business_baseline_hash": (
                str(business_baseline.baseline_hash)
                if business_baseline is not None
                else None
            ),
            "evidence_package_id": (
                str(business_baseline.evidence_package_id)
                if business_baseline is not None
                and business_baseline.evidence_package_id
                else None
            ),
            "evidence_package_hash": (
                str(business_baseline.evidence_package_hash)
                if business_baseline is not None
                and business_baseline.evidence_package_hash
                else None
            ),
            "verification_outcome": (
                str(business_baseline.verification_outcome)
                if business_baseline is not None
                else None
            ),
        }
    source_hashes = {
        "run_input_hash": str(run.input_hash),
        "frozen_config_ids": {
            "rule_set_id": str(run.rule_set_id),
            "fact_catalog_version_id": str(run.fact_catalog_version_id),
            "prompt_bundle_id": str(run.prompt_bundle_id),
            "tool_registry_version_id": str(run.tool_registry_version_id),
            "model_profile_version_id": str(run.model_profile_version_id),
            "formula_catalog_version_id": str(run.formula_catalog_version_id),
        },
        "manifest_hash": str(manifest.manifest_hash) if manifest is not None else None,
        "scope_hash": str(scope.scope_hash) if scope is not None else None,
        "enterprise_snapshot_hash": (
            str(snapshot.snapshot_hash) if snapshot is not None else None
        ),
        "enterprise_business_baseline_hash": (
            str(business_baseline.baseline_hash)
            if business_baseline is not None
            else None
        ),
        "enterprise_evidence_package_hash": (
            str(business_baseline.evidence_package_hash)
            if business_baseline is not None
            and business_baseline.evidence_package_hash
            else None
        ),
        "source_release_hash": (
            str(source_release.release_hash) if source_release is not None else None
        ),
        "report_hash": str(report.report_hash) if report is not None else None,
        "report_validation_hash": (
            str(report_validation.result_hash) if report_validation is not None else None
        ),
        "run_validation_hash": (
            str(run_validation.result_hash) if run_validation is not None else None
        ),
        "decision_hash": str(decision.decision_hash) if decision is not None else None,
        "gate_hashes": {
            code: str(gate_map[code].result_hash) if code in gate_map else None
            for code in MVP_RC_GATE_CODES
        },
        "active_run_id": str(assessment.active_run_id or "") or None,
        "current_manifest_id": str(assessment.current_manifest_id or "") or None,
        "latest_scope_id": str(latest_scope.id) if latest_scope is not None else None,
        "latest_enterprise_snapshot_id": (
            str(latest_snapshot.id) if latest_snapshot is not None else None
        ),
    }
    if run.hard_gate_comparison_baseline_id or run.hard_gate_comparison_baseline_hash:
        source_hashes.update(
            {
                "hard_gate_comparison_baseline_id": (
                    str(run.hard_gate_comparison_baseline_id)
                    if run.hard_gate_comparison_baseline_id
                    else None
                ),
                "hard_gate_comparison_baseline_hash": (
                    str(run.hard_gate_comparison_baseline_hash)
                    if run.hard_gate_comparison_baseline_hash
                    else None
                ),
            }
        )
    review_payload = {
        "review_note": review_note,
        "gate_reviews": reviewed_gates,
        "quality_reviews": reviewed_quality,
    }
    candidate_payload = {
        "schema": MVP_RC_VALIDATION_SCHEMA,
        "authority_version": MVP_RC_AUTHORITY_VERSION,
        "reviewer_id": int(actor_id),
        "assessment_id": str(run.assessment_id),
        "run_id": str(run.id),
        "source_hashes": source_hashes,
        "review": review_payload,
        "revalidation": revalidation,
        "acceptance_outcome": acceptance_outcome,
    }
    candidate_hash = canonical_hash(candidate_payload)
    existing = (
        db.query(BidMvpReleaseCandidate)
        .filter(BidMvpReleaseCandidate.run_id == run.id)
        .one_or_none()
    )
    can_freeze = not blocking_codes and not review_blocking_codes and existing is None
    return {
        "schema": MVP_RC_VALIDATION_SCHEMA,
        "authority_version": MVP_RC_AUTHORITY_VERSION,
        "assessment_id": str(run.assessment_id),
        "run_id": str(run.id),
        "report_id": str(report.id) if report is not None else None,
        "run_validation_id": str(run_validation.id) if run_validation is not None else None,
        "enterprise_snapshot_id": str(run.enterprise_snapshot_id),
        "decision": str(decision.decision) if decision is not None else None,
        "gate_outcomes": gate_outcomes,
        "acceptance_outcome": acceptance_outcome,
        "system_checks": checks,
        "blocking_codes": blocking_codes,
        "review_blocking_codes": sorted(review_blocking_codes),
        "review": review_payload,
        "revalidation": revalidation,
        "source_hashes": source_hashes,
        "candidate_hash": candidate_hash,
        "can_freeze": can_freeze,
        "already_frozen": existing is not None,
        "existing_release_candidate": (
            _release_projection(existing) if existing is not None else None
        ),
    }


def freeze_mvp_release_candidate(
    db: Session,
    *,
    actor_id: int,
    command: dict[str, Any],
    request_id: str,
    expected_candidate_hash: str,
    now: datetime | None = None,
) -> FrozenMvpReleaseCandidateResult:
    run_id = str(command.get("run_id") or "")
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == run_id)
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        raise BidMvpReleaseCandidateError("BID_MVP_RC_RUN_NOT_FOUND")
    db.query(BidAssessment).filter(BidAssessment.id == run.assessment_id).with_for_update().one()
    existing = (
        db.query(BidMvpReleaseCandidate)
        .filter(BidMvpReleaseCandidate.run_id == run.id)
        .with_for_update()
        .one_or_none()
    )
    preview = preview_mvp_release_candidate(db, actor_id=actor_id, command=command)
    normalized_expected = str(expected_candidate_hash or "").strip().lower()
    if normalized_expected != str(preview["candidate_hash"]):
        raise BidMvpReleaseCandidateError("BID_MVP_RC_CANDIDATE_HASH_MISMATCH")
    if existing is not None:
        if str(existing.candidate_hash) != normalized_expected:
            raise BidMvpReleaseCandidateError("BID_MVP_RC_ALREADY_FROZEN")
        return FrozenMvpReleaseCandidateResult(
            release=existing,
            created=False,
            projection=_release_projection(existing),
        )
    if not preview["can_freeze"]:
        raise BidMvpReleaseCandidateError("BID_MVP_RC_NOT_READY")
    if not preview["report_id"] or not preview["run_validation_id"]:
        raise BidMvpReleaseCandidateError("BID_MVP_RC_AUTHORITY_INCOMPLETE")

    current_time = as_utc(now) if now is not None else _database_utc_now(db)
    release_id = str(uuid.uuid5(_RELEASE_NAMESPACE, normalized_expected))
    version = f"mvp-rc-{current_time:%Y%m%d%H%M%S}-{normalized_expected[:12]}"
    manifest = {
        "schema": MVP_RC_SCHEMA,
        "authority_version": MVP_RC_AUTHORITY_VERSION,
        "release_candidate_id": release_id,
        "version": version,
        "assessment_id": str(run.assessment_id),
        "run_id": str(run.id),
        "report_id": str(preview["report_id"]),
        "run_validation_id": str(preview["run_validation_id"]),
        "enterprise_snapshot_id": str(run.enterprise_snapshot_id),
        "reviewer_id": int(actor_id),
        "reviewed_at": _utc_text(current_time),
        "acceptance_outcome": str(preview["acceptance_outcome"]),
        "candidate_hash": normalized_expected,
        "source_hashes": dict(preview["source_hashes"]),
        "revalidation": preview.get("revalidation"),
        "gate_outcomes": dict(preview["gate_outcomes"]),
        "system_checks": list(preview["system_checks"]),
    }
    release_hash = canonical_hash(manifest)
    row = BidMvpReleaseCandidate(
        id=release_id,
        version=version,
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        report_id=str(preview["report_id"]),
        run_validation_id=str(preview["run_validation_id"]),
        enterprise_snapshot_id=str(run.enterprise_snapshot_id),
        status="frozen",
        acceptance_outcome=str(preview["acceptance_outcome"]),
        reviewer_id=int(actor_id),
        review_note=str(command["review_note"]).strip(),
        review_json=dict(preview["review"]),
        source_hashes_json=dict(preview["source_hashes"]),
        manifest_json=manifest,
        candidate_hash=normalized_expected,
        release_hash=release_hash,
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
        action="mvp_release_candidate.freeze",
        entity_type="mvp_release_candidate",
        entity_id=release_id,
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=request_id,
        correlation_id=release_id,
        after={
            "run_id": str(run.id),
            "version": version,
            "acceptance_outcome": str(preview["acceptance_outcome"]),
            "candidate_hash": normalized_expected,
            "release_hash": release_hash,
        },
        metadata={
            "report_id": str(preview["report_id"]),
            "run_validation_id": str(preview["run_validation_id"]),
            "enterprise_snapshot_id": str(run.enterprise_snapshot_id),
        },
        occurred_at=current_time,
    )
    return FrozenMvpReleaseCandidateResult(
        release=row,
        created=True,
        projection=_release_projection(row),
    )
