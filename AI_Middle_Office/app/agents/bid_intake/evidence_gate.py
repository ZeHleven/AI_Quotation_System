from __future__ import annotations

from collections.abc import Iterable

from .contracts import (
    AssessmentDraft,
    DimensionStatus,
    DocumentManifest,
    EvidenceSufficiencyStatus,
    FactCoverageMode,
    FactCoverageState,
    FactSlotCoverageStatus,
    GateIssue,
    GateResult,
    GateStatus,
    PolicyEvaluation,
    PolicyFactorRating,
    PolicyFactorSource,
    Recommendation,
    REQUIRED_DIMENSIONS,
    Severity,
    ToolResultStatus,
)
from .ports import TenderEvidencePort


HIGH_RISK_LEVELS = {Severity.HIGH, Severity.CRITICAL}


def evaluate_evidence_gate(
    *,
    draft: AssessmentDraft,
    manifest: DocumentManifest,
    policy: PolicyEvaluation,
    evidence: TenderEvidencePort,
    repair_count: int,
    max_repairs: int,
    termination_reason: str | None = None,
    fact_coverage: FactCoverageState | None = None,
    fact_coverage_mode: FactCoverageMode = FactCoverageMode.OFF,
) -> GateResult:
    issues: list[GateIssue] = []
    review_by_dimension = {item.dimension: item for item in draft.dimension_reviews}

    for dimension in REQUIRED_DIMENSIONS:
        if dimension not in review_by_dimension:
            issues.append(
                GateIssue(
                    code="REQUIRED_DIMENSION_MISSING",
                    message=f"Required dimension is absent: {dimension}",
                    path=f"dimension_reviews.{dimension}",
                    repairable=True,
                )
            )

    refs = draft.collect_evidence_refs()
    validation = evidence.validate_refs(refs=refs, manifest=manifest)
    validation_rows: list[dict] = []
    if validation.status != ToolResultStatus.OK or not isinstance(validation.data, dict):
        issues.append(
            GateIssue(
                code="EVIDENCE_VALIDATION_UNAVAILABLE",
                message="Evidence references could not be validated.",
                repairable=False,
            )
        )
    else:
        validation_rows = list(validation.data.get("results") or [])

    validation_by_id = {str(row.get("evidence_id")): row for row in validation_rows}
    for ref in refs:
        result = validation_by_id.get(ref.evidence_id)
        if not result or not result.get("valid"):
            reasons = ", ".join((result or {}).get("reasons") or ["validation_result_missing"])
            issues.append(
                GateIssue(
                    code="EVIDENCE_REF_INVALID",
                    message=f"Evidence {ref.evidence_id} is invalid: {reasons}",
                    path=f"evidence_refs.{ref.evidence_id}",
                    repairable=True,
                )
            )

    high_risk_findings = [
        finding
        for finding in [*draft.key_findings, *draft.risks]
        if finding.severity in HIGH_RISK_LEVELS
    ]
    for finding in high_risk_findings:
        if not finding.evidence_refs:
            issues.append(
                GateIssue(
                    code="HIGH_RISK_EVIDENCE_MISSING",
                    message=f"High-risk claim has no evidence: {finding.claim_id}",
                    path=f"findings.{finding.claim_id}",
                    repairable=True,
                )
            )
            continue
        unread = [
            ref.evidence_id
            for ref in finding.evidence_refs
            if not validation_by_id.get(ref.evidence_id, {}).get("context_read_traced")
        ]
        if unread:
            issues.append(
                GateIssue(
                    code="HIGH_RISK_CONTEXT_NOT_READ",
                    message=f"High-risk claim used evidence without a context read: {', '.join(unread)}",
                    path=f"findings.{finding.claim_id}",
                    repairable=True,
                )
            )

    for factor in draft.policy_factors:
        if (
            factor.rating == PolicyFactorRating.UNKNOWN
            or factor.source_type
            != PolicyFactorSource.TENDER_EVIDENCE
        ):
            continue
        if not factor.evidence_refs:
            issues.append(
                GateIssue(
                    code="POLICY_FACTOR_EVIDENCE_MISSING",
                    message=(
                        "Tender-derived policy factor has no evidence: "
                        f"{factor.factor_id}"
                    ),
                    path=f"policy_factors.{factor.factor_id}",
                    repairable=True,
                )
            )
            continue
        if factor.rating not in {
            PolicyFactorRating.ADVERSE,
            PolicyFactorRating.CRITICAL,
        }:
            continue
        unread = [
            ref.evidence_id
            for ref in factor.evidence_refs
            if not validation_by_id.get(
                ref.evidence_id,
                {},
            ).get("context_read_traced")
        ]
        if unread:
            issues.append(
                GateIssue(
                    code="POLICY_FACTOR_CONTEXT_NOT_READ",
                    message=(
                        "Adverse policy factor used evidence without "
                        f"a context read: {', '.join(unread)}"
                    ),
                    path=f"policy_factors.{factor.factor_id}",
                    repairable=True,
                )
            )

    if draft.missing_materials or policy.required_document_gaps:
        gaps = [item.document_type for item in draft.missing_materials]
        gaps.extend(policy.required_document_gaps)
        issues.append(
            GateIssue(
                code="REQUIRED_MATERIAL_MISSING",
                message=f"Required materials are missing: {', '.join(sorted(set(gaps)))}",
                path="missing_materials",
                repairable=False,
            )
        )

    unresolved_statuses = {DimensionStatus.MISSING, DimensionStatus.CONFLICT, DimensionStatus.UNRESOLVED}
    unresolved = [
        item.dimension
        for item in draft.dimension_reviews
        if item.status in unresolved_statuses
    ]
    if draft.recommendation == Recommendation.RECOMMEND_QUOTE and unresolved:
        issues.append(
            GateIssue(
                code="RECOMMENDATION_INCONSISTENT",
                message=f"Cannot recommend quoting while dimensions remain unresolved: {', '.join(unresolved)}",
                path="recommendation",
                repairable=False,
            )
        )

    if policy.status in {"special_approval_required", "not_evaluable"}:
        issues.append(
            GateIssue(
                code="POLICY_REQUIRES_MANUAL_REVIEW",
                message=f"Policy evaluation status is {policy.status}.",
                path="policy_evaluation.status",
                repairable=False,
            )
        )

    if termination_reason and termination_reason not in {"analysis_complete"}:
        issues.append(
            GateIssue(
                code="AGENT_TERMINATED_EARLY",
                message=f"Agent did not finish normally: {termination_reason}",
                path="termination_reason",
                repairable=False,
            )
        )

    if (
        fact_coverage_mode == FactCoverageMode.ENFORCED
        and fact_coverage is not None
        and fact_coverage.sufficiency_status
        == EvidenceSufficiencyStatus.INSUFFICIENT
    ):
        uncovered = [
            item.label
            for item in fact_coverage.slots
            if item.status == FactSlotCoverageStatus.UNCOVERED
        ]
        issues.append(
            GateIssue(
                code="FACT_SLOT_EVIDENCE_INSUFFICIENT",
                message=(
                    "Retrieval evidence does not cover all assessed facts: "
                    + ", ".join(uncovered)
                ),
                path="fact_coverage",
                repairable=False,
            )
        )
    if (
        fact_coverage_mode == FactCoverageMode.ENFORCED
        and (
            fact_coverage is None
            or fact_coverage.sufficiency_status
            == EvidenceSufficiencyStatus.NOT_ASSESSED
        )
    ):
        issues.append(
            GateIssue(
                code="FACT_SLOT_EVIDENCE_NOT_ASSESSED",
                message=(
                    "Evidence sufficiency could not be assessed "
                    "reliably; manual review is required."
                ),
                path="fact_coverage",
                repairable=False,
            )
        )

    status = _resolve_gate_status(
        issues,
        repair_count=repair_count,
        max_repairs=max_repairs,
    )
    return GateResult(
        status=status,
        issues=issues,
        checked_evidence_ids=[item.evidence_id for item in refs],
        fact_coverage_mode=fact_coverage_mode,
        fact_coverage_status=(
            fact_coverage.sufficiency_status
            if fact_coverage is not None
            else EvidenceSufficiencyStatus.NOT_ASSESSED
        ),
        fact_coverage_rate=(
            fact_coverage.coverage_rate
            if fact_coverage is not None
            else None
        ),
    )


def _resolve_gate_status(
    issues: Iterable[GateIssue],
    *,
    repair_count: int,
    max_repairs: int,
) -> GateStatus:
    issue_list = list(issues)
    if not issue_list:
        return GateStatus.PASSED
    if any(item.code == "REQUIRED_MATERIAL_MISSING" for item in issue_list):
        return GateStatus.SUPPLEMENT_REQUIRED
    if any(item.repairable for item in issue_list):
        if repair_count < max_repairs:
            return GateStatus.REPAIR_REQUIRED
        return GateStatus.MANUAL_REVIEW_REQUIRED
    return GateStatus.MANUAL_REVIEW_REQUIRED
