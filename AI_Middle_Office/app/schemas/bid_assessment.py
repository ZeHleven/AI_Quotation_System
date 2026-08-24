"""Strict external request models for the bid-assessment v1 facade."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BidAssessmentCreateIn(BaseModel):
    """Frozen API-01 request body.

    Keep this model aligned with ``CreateAssessmentRequest`` in the Phase 0
    machine contract. Unknown fields are deliberately rejected.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    client_name: str = Field(min_length=1, max_length=300)
    internal_note: str | None = Field(default=None, max_length=2000)
    external_ref: str | None = Field(default=None, max_length=100)

    @field_validator("title", "client_name")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("internal_note", "external_ref")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BidUploadBatchCreateIn(BaseModel):
    """Frozen API-10 request body."""

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["initial", "change"]
    base_manifest_id: str | None = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    @model_validator(mode="after")
    def _validate_manifest_pair(self):
        if self.purpose == "initial" and self.base_manifest_id is not None:
            raise ValueError("base_manifest_id must be null for initial batches")
        if self.purpose == "change" and self.base_manifest_id is None:
            raise ValueError("base_manifest_id is required for change batches")
        return self


class BidUploadFileCreateIn(BaseModel):
    """Frozen non-binary API-12 multipart fields."""

    model_config = ConfigDict(extra="forbid")

    client_file_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    operation: Literal["add", "replace"]
    replace_document_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    relative_path: str | None = Field(default=None, max_length=1000)

    @field_validator("relative_path")
    @classmethod
    def _normalize_relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().replace("\\", "/")
        if not normalized:
            return None
        if normalized.startswith("/") or any(
            segment in {"", ".", ".."} for segment in normalized.split("/")
        ):
            raise ValueError("relative_path must be a safe relative display path")
        if any(ord(character) < 0x20 for character in normalized):
            raise ValueError("relative_path contains control characters")
        return normalized

    @model_validator(mode="after")
    def _validate_replace_target(self):
        if self.operation == "add" and self.replace_document_id is not None:
            raise ValueError("replace_document_id must be null for add")
        if self.operation == "replace" and self.replace_document_id is None:
            raise ValueError("replace_document_id is required for replace")
        return self


_BidResourceId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    ),
]


class BidUploadBatchDeactivationCreateIn(BaseModel):
    """Frozen API-14 request body with order-insensitive document IDs."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[_BidResourceId] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("document_ids")
    @classmethod
    def _normalize_document_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("document_ids must be unique")
        return sorted(value)

    @field_validator("reason")
    @classmethod
    def _normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        return normalized


class BidUploadBatchCommitIn(BaseModel):
    """Frozen API-15 commit confirmation body."""

    model_config = ConfigDict(extra="forbid")

    expected_file_count: int = Field(ge=0)
    expected_deactivation_count: int = Field(ge=0)
    change_note: str | None = Field(max_length=1000)
    confirm_start_analysis: Literal[True]

    @field_validator("change_note")
    @classmethod
    def _normalize_change_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BidUploadBatchAbandonIn(BaseModel):
    """Frozen API-16 explicit abandonment reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        return normalized


class BidLotSelectionIn(BaseModel):
    """Frozen API-31 request body."""

    model_config = ConfigDict(extra="forbid")

    manifest_id: _BidResourceId
    lot_id: _BidResourceId
    selection_note: str | None = Field(max_length=1000)

    @field_validator("selection_note")
    @classmethod
    def _normalize_selection_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BidAssessmentCloneForLotIn(BaseModel):
    """Frozen API-32 request body."""

    model_config = ConfigDict(extra="forbid")

    source_manifest_id: _BidResourceId
    lot_id: _BidResourceId
    title: str = Field(min_length=1, max_length=300)

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class BidRunCreateIn(BaseModel):
    """Frozen API-40 manual reanalysis request body."""

    model_config = ConfigDict(extra="forbid")

    manifest_id: _BidResourceId
    reason: Literal[
        "manual_restart",
        "new_enterprise_snapshot",
        "rule_reanalysis",
    ]
    note: str | None = Field(max_length=1000)

    @field_validator("note")
    @classmethod
    def _normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BidRunCancelIn(BaseModel):
    """Frozen API-42 cancellation request body."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class BidRunRetryIn(BaseModel):
    """Frozen API-43 checkpoint retry request body."""

    model_config = ConfigDict(extra="forbid")

    retry_mode: Literal["from_latest_checkpoint"]
    note: str | None = Field(max_length=1000)

    @field_validator("note")
    @classmethod
    def _normalize_retry_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


_ENTERPRISE_SLOT_CODES = Literal[
    "I01",
    "I02",
    "I03",
    "I04",
    "I05",
    "I06",
    "I07",
    "I08",
    "I09",
    "I10",
    "I11",
]


class BidEnterpriseCapabilityRecordIn(BaseModel):
    """One aggregate I01-I11 record in an immutable enterprise snapshot."""

    model_config = ConfigDict(extra="forbid")

    slot_code: _ENTERPRISE_SLOT_CODES
    coverage_status: Literal["supported", "partial", "unknown"]
    value: Any | None = None
    source_record_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    source_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    source_status: Literal["verified", "self_reported", "imported", "unknown"]
    source_label: str = Field(min_length=1, max_length=300)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    checked_at: datetime | None = None

    @field_validator("source_label")
    @classmethod
    def _normalize_source_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source_label must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_enterprise_record(self):
        if self.coverage_status == "unknown" and self.value is not None:
            raise ValueError("unknown records must not carry a value")
        if self.coverage_status != "unknown" and self.value is None:
            raise ValueError("supported or partial records require a value")
        if self.coverage_status == "supported" and self.source_status == "unknown":
            raise ValueError("supported records require a governed source status")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("valid_to must not be earlier than valid_from")
        return self


class BidEnterpriseCapabilitySnapshotCreateIn(BaseModel):
    """Phase 4C-1 immutable enterprise capability snapshot command."""

    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    change_note: str = Field(min_length=1, max_length=1000)
    records: list[BidEnterpriseCapabilityRecordIn] = Field(min_length=11, max_length=11)

    @field_validator("change_note")
    @classmethod
    def _normalize_change_note(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("change_note must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_slot_set(self):
        expected = {f"I{index:02d}" for index in range(1, 12)}
        actual = [record.slot_code for record in self.records]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("records must contain every I01-I11 slot exactly once")
        return self


class BidEnterpriseBusinessSlotReviewIn(BaseModel):
    """One Phase 4D-1 business review of an I01-I11 snapshot slot."""

    model_config = ConfigDict(extra="forbid")

    slot_code: _ENTERPRISE_SLOT_CODES
    disposition: Literal["confirmed", "correction_required", "not_reviewed"]
    evidence_class: Literal[
        "official_document",
        "internal_system",
        "audited_record",
        "management_attestation",
        "not_available",
    ]
    evidence_ref: str | None = Field(default=None, max_length=300)
    evidence_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_item_id: str | None = Field(
        default=None,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("evidence_ref", "note")
    @classmethod
    def _normalize_optional_business_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class BidEnterpriseBusinessBaselineCreateIn(BaseModel):
    """Phase 4D-1 immutable business-baseline verification command."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    evidence_package_id: str | None = Field(
        default=None,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    reviewed_as_of: datetime
    review_note: str = Field(min_length=1, max_length=2000)
    slot_reviews: list[BidEnterpriseBusinessSlotReviewIn] = Field(
        min_length=11,
        max_length=11,
    )

    @field_validator("review_note")
    @classmethod
    def _normalize_business_review_note(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("review_note must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_business_slot_set(self):
        expected = {f"I{index:02d}" for index in range(1, 12)}
        actual = [review.slot_code for review in self.slot_reviews]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("slot_reviews must contain every I01-I11 slot exactly once")
        return self


class BidEnterpriseEvidencePackageSlotIn(BaseModel):
    """Explicit mapping from governed evidence items to one capability slot."""

    model_config = ConfigDict(extra="forbid")

    slot_code: _ENTERPRISE_SLOT_CODES
    evidence_item_ids: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("evidence_item_ids")
    @classmethod
    def _normalize_evidence_item_ids(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values]
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", value)
            for value in normalized
        ):
            raise ValueError("evidence_item_ids contain an invalid identifier")
        if len(normalized) != len(set(normalized)):
            raise ValueError("evidence_item_ids must be unique within one slot")
        return sorted(normalized)

    @field_validator("note")
    @classmethod
    def _normalize_package_slot_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _require_unknown_note(self):
        if not self.evidence_item_ids and not self.note:
            raise ValueError("an unmapped slot requires an explicit note")
        return self


class BidEnterpriseEvidencePackageCreateIn(BaseModel):
    """Phase 4D-2 immutable enterprise evidence package command."""

    model_config = ConfigDict(extra="forbid")

    package_label: str = Field(min_length=1, max_length=300)
    as_of: datetime
    change_note: str = Field(min_length=1, max_length=2000)
    slots: list[BidEnterpriseEvidencePackageSlotIn] = Field(
        min_length=11,
        max_length=11,
    )

    @field_validator("package_label", "change_note")
    @classmethod
    def _normalize_package_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_package_slot_set(self):
        expected = {f"I{index:02d}" for index in range(1, 12)}
        actual = [slot.slot_code for slot in self.slots]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("slots must contain every I01-I11 slot exactly once")
        return self


_HARD_GATE_COMPARABLE_FACT_SIDES = {
    "tender.overview": "tender",
    "tender.submission.deadline": "tender",
    "tender.qualification.requirements": "tender",
    "tender.guarantee.requirements": "tender",
    "tender.schedule.site_constraints": "tender",
    "enterprise.identity.legal_name": "enterprise",
    "enterprise.qualifications.active_records": "enterprise",
    "enterprise.safety_license.active_record": "enterprise",
    "enterprise.performance.records": "enterprise",
    "enterprise.personnel.available_records": "enterprise",
    "enterprise.financial.capacity": "enterprise",
    "enterprise.guarantee.capacity": "enterprise",
    "enterprise.bid_preparation.capacity": "enterprise",
    "enterprise.prohibited_risk.rules": "enterprise",
    "enterprise.compliance.current_records": "enterprise",
    "enterprise.client_risk.current_records": "enterprise",
}

_HARD_GATE_COMPARABLE_FACT_SLOTS = Literal[
    "tender.overview",
    "tender.submission.deadline",
    "tender.qualification.requirements",
    "tender.guarantee.requirements",
    "tender.schedule.site_constraints",
    "enterprise.identity.legal_name",
    "enterprise.qualifications.active_records",
    "enterprise.safety_license.active_record",
    "enterprise.performance.records",
    "enterprise.personnel.available_records",
    "enterprise.financial.capacity",
    "enterprise.guarantee.capacity",
    "enterprise.bid_preparation.capacity",
    "enterprise.prohibited_risk.rules",
    "enterprise.compliance.current_records",
    "enterprise.client_risk.current_records",
]


class BidHardGateComparableFactIn(BaseModel):
    """One human-verified canonical input to deterministic HG01-HG07."""

    model_config = ConfigDict(extra="forbid")

    fact_slot: _HARD_GATE_COMPARABLE_FACT_SLOTS
    source_side: Literal["tender", "enterprise"]
    verification_status: Literal["supported", "partial", "unknown"]
    value_type: str | None = Field(default=None, max_length=48)
    canonical_value: Any | None = None
    evidence_item_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence_atom_ids: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("value_type", "note")
    @classmethod
    def _normalize_comparable_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("evidence_item_ids", "evidence_atom_ids")
    @classmethod
    def _normalize_comparable_evidence_ids(cls, values: list[str]) -> list[str]:
        normalized = sorted({str(value).strip() for value in values})
        if len(normalized) != len(values) or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", value)
            for value in normalized
        ):
            raise ValueError("evidence identifiers must be unique governed IDs")
        return normalized

    @model_validator(mode="after")
    def _validate_comparable_fact(self):
        expected_side = _HARD_GATE_COMPARABLE_FACT_SIDES[str(self.fact_slot)]
        if self.source_side != expected_side:
            raise ValueError("source_side does not match fact_slot")
        if not self.note:
            raise ValueError("note must not be blank")
        if self.verification_status == "unknown":
            if self.canonical_value is not None or self.value_type is not None:
                raise ValueError("unknown facts must not carry a canonical value")
            if self.evidence_item_ids or self.evidence_atom_ids:
                raise ValueError("unknown facts must not claim authoritative evidence")
            return self
        if self.canonical_value is None or not self.value_type:
            raise ValueError("supported or partial facts require value_type and canonical_value")
        if self.source_side == "tender":
            if not self.evidence_atom_ids or self.evidence_item_ids:
                raise ValueError("tender facts require Atom evidence only")
        elif not self.evidence_item_ids or self.evidence_atom_ids:
            raise ValueError("enterprise facts require Evidence Item lineage only")
        return self


class BidHardGateComparisonBaselineCreateIn(BaseModel):
    """Phase 4D-3 zero-persist validate and immutable freeze command."""

    model_config = ConfigDict(extra="forbid")

    assessment_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    source_run_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    business_baseline_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    reviewed_as_of: datetime
    review_note: str = Field(min_length=1, max_length=2000)
    facts: list[BidHardGateComparableFactIn] = Field(min_length=16, max_length=16)

    @field_validator("review_note")
    @classmethod
    def _normalize_comparison_review_note(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("review_note must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_comparable_fact_set(self):
        actual = [str(fact.fact_slot) for fact in self.facts]
        expected = set(_HARD_GATE_COMPARABLE_FACT_SIDES)
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("facts must contain every hard-gate comparable slot exactly once")
        return self


_MVP_GATE_CODES = Literal["HG01", "HG02", "HG03", "HG04", "HG05", "HG06", "HG07"]
_MVP_REVIEW_DISPOSITIONS = Literal[
    "confirmed",
    "correction_required",
    "not_reviewed",
]
_MVP_QUALITY_CODES = Literal[
    "REPORT_BUSINESS_READABLE",
    "CITATIONS_TRACEABLE",
    "UNKNOWNS_EXPLICIT",
    "DECISION_REASONABLE",
    "PARSE_LIMITATIONS_REVIEWED",
]


class BidMvpGateReviewIn(BaseModel):
    """One human review of a deterministic hard-gate outcome."""

    model_config = ConfigDict(extra="forbid")

    gate_code: _MVP_GATE_CODES
    disposition: _MVP_REVIEW_DISPOSITIONS
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def _normalize_optional_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _require_correction_note(self):
        if self.disposition == "correction_required" and self.note is None:
            raise ValueError("correction_required gate reviews require a note")
        return self


class BidMvpQualityReviewIn(BaseModel):
    """One bounded reviewer check for the generated report package."""

    model_config = ConfigDict(extra="forbid")

    code: _MVP_QUALITY_CODES
    disposition: _MVP_REVIEW_DISPOSITIONS
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def _normalize_quality_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _require_correction_note(self):
        if self.disposition == "correction_required" and self.note is None:
            raise ValueError("correction_required quality reviews require a note")
        return self


class BidMvpReleaseCandidateCreateIn(BaseModel):
    """Phase 4C-3 zero-persist preview and immutable freeze command."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$",
    )
    source_release_candidate_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$",
    )
    review_note: str = Field(min_length=1, max_length=2000)
    gate_reviews: list[BidMvpGateReviewIn] = Field(min_length=7, max_length=7)
    quality_reviews: list[BidMvpQualityReviewIn] = Field(min_length=5, max_length=5)

    @field_validator("review_note")
    @classmethod
    def _normalize_review_note(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("review_note must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_review_sets(self):
        expected_gates = {f"HG{index:02d}" for index in range(1, 8)}
        gate_codes = [review.gate_code for review in self.gate_reviews]
        if len(gate_codes) != len(set(gate_codes)) or set(gate_codes) != expected_gates:
            raise ValueError("gate_reviews must contain HG01-HG07 exactly once")
        expected_quality = {
            "REPORT_BUSINESS_READABLE",
            "CITATIONS_TRACEABLE",
            "UNKNOWNS_EXPLICIT",
            "DECISION_REASONABLE",
            "PARSE_LIMITATIONS_REVIEWED",
        }
        quality_codes = [review.code for review in self.quality_reviews]
        if (
            len(quality_codes) != len(set(quality_codes))
            or set(quality_codes) != expected_quality
        ):
            raise ValueError("quality_reviews must contain every MVP quality code once")
        return self
