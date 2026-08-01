from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Recommendation(str, Enum):
    RECOMMEND_QUOTE = "recommend_quote"
    RECOMMEND_NO_QUOTE = "recommend_no_quote"
    NEED_SUPPLEMENT = "need_supplement"
    MANUAL_REVIEW = "manual_review"


class DimensionStatus(str, Enum):
    CONFIRMED = "confirmed"
    MISSING = "missing"
    CONFLICT = "conflict"
    UNRESOLVED = "unresolved"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyFactorRating(str, Enum):
    FAVORABLE = "favorable"
    ACCEPTABLE = "acceptable"
    ADVERSE = "adverse"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class PolicyFactorSource(str, Enum):
    TENDER_EVIDENCE = "tender_evidence"
    INTERNAL_DATA = "internal_data"
    HUMAN_INPUT = "human_input"
    UNKNOWN = "unknown"


class PolicyDecision(str, Enum):
    RECOMMEND_QUOTE = "recommend_quote"
    CONDITIONAL_QUOTE = "conditional_quote"
    RECOMMEND_NO_QUOTE = "recommend_no_quote"
    NEED_SUPPLEMENT = "need_supplement"
    MANUAL_REVIEW = "manual_review"


class GateStatus(str, Enum):
    PASSED = "passed"
    RESEARCH_RESTART_REQUIRED = "research_restart_required"
    REPAIR_REQUIRED = "repair_required"
    SUPPLEMENT_REQUIRED = "supplement_required"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class HumanAction(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_CONDITIONS = "approved_with_conditions"
    REJECTED = "rejected"
    SUPPLEMENT_REQUESTED = "supplement_requested"
    RESEARCH_REQUESTED = "research_requested"


class ToolResultStatus(str, Enum):
    OK = "ok"
    NO_RESULT = "no_result"
    PARTIAL = "partial"
    FAILED = "failed"


class FactCoverageMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCED = "enforced"


class FactSlotCoverageStatus(str, Enum):
    UNCOVERED = "uncovered"
    CANDIDATE_COVERED = "candidate_covered"
    CONTEXT_VERIFIED = "context_verified"


class EvidenceSufficiencyStatus(str, Enum):
    NOT_ASSESSED = "not_assessed"
    CANDIDATE_SUFFICIENT = "candidate_sufficient"
    INSUFFICIENT = "insufficient"


class EvidenceLocator(StrictModel):
    page: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    cell_range: str | None = None
    section: str | None = None


class EvidenceRef(StrictModel):
    evidence_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    locator: EvidenceLocator
    content_hash: str = Field(min_length=8)
    context_read: bool = False
    quote: str | None = Field(default=None, max_length=500)


class FactSlotCoverage(StrictModel):
    slot_id: str = Field(min_length=8, max_length=64)
    label: str = Field(min_length=1, max_length=500)
    slot_type: str = Field(min_length=1, max_length=80)
    status: FactSlotCoverageStatus
    candidate_evidence_ids: list[str] = Field(default_factory=list)
    verified_evidence_ids: list[str] = Field(default_factory=list)
    source_trace_ids: list[str] = Field(default_factory=list)


class FactCoverageState(StrictModel):
    schema_version: Literal["bid_intake_fact_coverage_v1"] = (
        "bid_intake_fact_coverage_v1"
    )
    mode: FactCoverageMode = FactCoverageMode.SHADOW
    sufficiency_status: EvidenceSufficiencyStatus = (
        EvidenceSufficiencyStatus.NOT_ASSESSED
    )
    required_slot_count: int = Field(default=0, ge=0)
    covered_slot_count: int = Field(default=0, ge=0)
    verified_slot_count: int = Field(default=0, ge=0)
    coverage_rate: float | None = Field(default=None, ge=0, le=1)
    evaluated_search_count: int = Field(default=0, ge=0)
    observed_search_count: int = Field(default=0, ge=0)
    slots: list[FactSlotCoverage] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DocumentManifestItem(StrictModel):
    document_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    sha256: str = Field(min_length=8)
    parse_status: Literal["ready", "partial", "failed"]
    active: bool = True


class DocumentManifest(StrictModel):
    case_id: str = Field(min_length=1)
    manifest_version: int = Field(ge=1)
    manifest_hash: str = Field(min_length=8)
    documents: list[DocumentManifestItem] = Field(default_factory=list)

    @property
    def active_documents(self) -> list[DocumentManifestItem]:
        return [item for item in self.documents if item.active]


class ProjectFact(StrictModel):
    field: str = Field(min_length=1)
    value: str = Field(min_length=1)
    normalized_value: str | float | int | bool | None = None
    unit: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class Finding(StrictModel):
    claim_id: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    title: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    severity: Severity
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class DimensionReview(StrictModel):
    dimension: str = Field(min_length=1)
    status: DimensionStatus
    summary: str = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class MissingMaterial(StrictModel):
    document_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    blocks_decision: bool = True


class ConflictItem(StrictModel):
    topic: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class PolicyFactorInput(StrictModel):
    factor_id: str = Field(min_length=1)
    rating: PolicyFactorRating
    summary: str = Field(min_length=1)
    source_type: PolicyFactorSource
    source_note: str | None = Field(default=None, max_length=500)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self) -> "PolicyFactorInput":
        if (
            self.rating == PolicyFactorRating.UNKNOWN
            and self.source_type != PolicyFactorSource.UNKNOWN
        ):
            raise ValueError("unknown factor must use unknown source")
        if (
            self.rating != PolicyFactorRating.UNKNOWN
            and self.source_type == PolicyFactorSource.UNKNOWN
        ):
            raise ValueError("known factor must identify its source")
        if self.source_type in {
            PolicyFactorSource.INTERNAL_DATA,
            PolicyFactorSource.HUMAN_INPUT,
        } and not str(self.source_note or "").strip():
            raise ValueError(
                "internal or human factor requires source_note"
            )
        return self


class AssessmentDraft(StrictModel):
    project_summary: str = Field(min_length=1)
    recommendation: Recommendation
    project_facts: list[ProjectFact] = Field(default_factory=list)
    dimension_reviews: list[DimensionReview] = Field(min_length=1)
    key_findings: list[Finding] = Field(default_factory=list)
    missing_materials: list[MissingMaterial] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    risks: list[Finding] = Field(default_factory=list)
    policy_factors: list[PolicyFactorInput] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    termination_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def ensure_unique_dimensions(self) -> "AssessmentDraft":
        dimensions = [item.dimension for item in self.dimension_reviews]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("dimension_reviews contains duplicate dimensions")
        factor_ids = [item.factor_id for item in self.policy_factors]
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("policy_factors contains duplicate factors")
        return self

    def collect_evidence_refs(self) -> list[EvidenceRef]:
        refs: dict[str, EvidenceRef] = {}
        for fact in self.project_facts:
            for ref in fact.evidence_refs:
                refs[ref.evidence_id] = ref
        for review in self.dimension_reviews:
            for ref in review.evidence_refs:
                refs[ref.evidence_id] = ref
        for finding in [*self.key_findings, *self.risks]:
            for ref in finding.evidence_refs:
                refs[ref.evidence_id] = ref
        for conflict in self.conflicts:
            for ref in conflict.evidence_refs:
                refs[ref.evidence_id] = ref
        for factor in self.policy_factors:
            for ref in factor.evidence_refs:
                refs[ref.evidence_id] = ref
        return list(refs.values())


class PolicyRuleHit(StrictModel):
    rule_id: str
    level: Literal["warning", "hard"]
    message: str
    factor_id: str | None = None
    outcome: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class PolicyFactorResult(StrictModel):
    factor_id: str
    name: str
    weight: float = Field(gt=0, le=100)
    rating: PolicyFactorRating
    rating_score: float = Field(ge=0, le=100)
    weighted_score: float = Field(ge=0, le=100)
    known: bool
    critical_unknown: bool
    summary: str
    source_type: PolicyFactorSource
    source_note: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class PolicyEvaluation(StrictModel):
    policy_version: str
    status: Literal[
        "passed",
        "warning",
        "special_approval_required",
        "not_evaluable",
    ]
    decision: PolicyDecision = PolicyDecision.MANUAL_REVIEW
    hard_rule_hits: list[PolicyRuleHit] = Field(default_factory=list)
    warning_rule_hits: list[PolicyRuleHit] = Field(default_factory=list)
    required_document_gaps: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0, le=100)
    coverage: float = Field(default=0, ge=0, le=100)
    factor_results: list[PolicyFactorResult] = Field(default_factory=list)
    critical_unknown_factors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class GateIssue(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str | None = None
    repairable: bool = False


class GateResult(StrictModel):
    status: GateStatus
    issues: list[GateIssue] = Field(default_factory=list)
    checked_evidence_ids: list[str] = Field(default_factory=list)
    fact_coverage_mode: FactCoverageMode = FactCoverageMode.OFF
    fact_coverage_status: EvidenceSufficiencyStatus = (
        EvidenceSufficiencyStatus.NOT_ASSESSED
    )
    fact_coverage_rate: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    gate_version: str = "evidence_gate_v3"


class HumanDecision(StrictModel):
    decision_id: str = Field(min_length=1)
    action: HumanAction
    report_version: int = Field(ge=1)
    manifest_version: int = Field(ge=1)
    decided_by: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=2000)
    conditions: list[str] = Field(default_factory=list)


class ToolResult(StrictModel):
    status: ToolResultStatus
    data: Any = None
    retryable: bool = False
    trace_id: str = Field(min_length=1)
    error_code: str | None = None
    message: str | None = None


class AgentVersions(StrictModel):
    graph_version: str = "bid_intake_graph_v4"
    state_schema_version: str = "bid_intake_state_v4"
    prompt_version: str = "bid_intake_prompt_v3"
    policy_version: str = "qs_bid_decision_policy_2026_01"
    tool_schema_version: str = "tender_evidence_tools_v1"
    model_id: str = "scripted-demo-model"


REQUIRED_DIMENSIONS: tuple[str, ...] = (
    "project_basics",
    "deadline",
    "scope",
    "qualification",
    "schedule",
    "payment",
    "bond",
    "submission_requirements",
    "document_completeness",
    "version_conflicts",
)
