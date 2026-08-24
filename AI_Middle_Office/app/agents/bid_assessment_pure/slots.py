"""Slot, pending, validation, and continuation checkpoint contracts."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .common import Reference, StrictContract
from .planning import ExecutionMode


class SlotStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


class PendingPhase(str, Enum):
    WAITING_INPUT = "waiting_input"
    VALIDATING_FORMAT = "validating_format"
    VALIDATING_BUSINESS = "validating_business"


class ValidationStage(str, Enum):
    FORMAT = "format_validation"
    BUSINESS = "business_validation"


class CheckpointStatus(str, Enum):
    OPEN = "open"
    CONSUMED = "consumed"
    INVALIDATED = "invalidated"


class Slot(StrictContract):
    slot_id: Reference
    task_id: Reference
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    request_message: str = Field(min_length=1, max_length=2000)
    input_model_ref: Reference
    business_validator_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    status: SlotStatus
    candidate_input_ref: Reference | None
    resolved_value_ref: Reference | None

    @model_validator(mode="after")
    def validate_resolution(self) -> "Slot":
        if len(self.business_validator_refs) != len(set(self.business_validator_refs)):
            raise ValueError("business_validator_refs must be unique")
        if self.status is SlotStatus.RESOLVED and self.resolved_value_ref is None:
            raise ValueError("resolved slot requires resolved_value_ref")
        if self.status is SlotStatus.UNRESOLVED and self.resolved_value_ref is not None:
            raise ValueError("unresolved slot forbids resolved_value_ref")
        return self


class PendingContext(StrictContract):
    slot_ref: Reference
    checkpoint_ref: Reference
    phase: PendingPhase
    validation_attempt_ref: Reference | None
    last_error_ref: Reference | None

    @model_validator(mode="after")
    def validate_phase_refs(self) -> "PendingContext":
        validating = self.phase in {
            PendingPhase.VALIDATING_FORMAT,
            PendingPhase.VALIDATING_BUSINESS,
        }
        if validating and self.validation_attempt_ref is None:
            raise ValueError("validation phase requires validation_attempt_ref")
        return self


class SlotValidationIssue(StrictContract):
    slot_id: Reference
    stage: ValidationStage
    code: str = Field(min_length=1, max_length=100, pattern=r"^[A-Z][A-Z0-9_]*$")
    field: str | None = Field(default=None, min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=500)
    guidance: str = Field(min_length=1, max_length=1000)
    retryable: bool


class SlotValidationOutcome(StrictContract):
    slot_id: Reference
    stage: ValidationStage
    accepted: bool
    candidate_input_ref: Reference
    resolved_value_ref: Reference | None
    issues: tuple[SlotValidationIssue, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def validate_result_shape(self) -> "SlotValidationOutcome":
        if self.accepted and (self.resolved_value_ref is None or self.issues):
            raise ValueError("accepted validation requires a value and forbids issues")
        if not self.accepted and (self.resolved_value_ref is not None or not self.issues):
            raise ValueError("rejected validation requires issues and forbids a value")
        if any(issue.slot_id != self.slot_id for issue in self.issues):
            raise ValueError("validation issues must reference the same slot")
        return self


class ContinuationCheckpoint(StrictContract):
    checkpoint_id: Reference
    task_id: Reference
    slot_ref: Reference
    suspended_state_version: int = Field(ge=1)
    execution_mode: ExecutionMode
    context_snapshot_ref: Reference
    suspended_action_ref: Reference
    effect_fence_ref: Reference
    resume_token_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: CheckpointStatus


class ResumeProof(StrictContract):
    slot_ref: Reference
    checkpoint_ref: Reference
    resolved_value_ref: Reference
    resume_token_verified: bool
    effect_fence_verified: bool
    checkpoint_consumed: bool

    @model_validator(mode="after")
    def validate_proof(self) -> "ResumeProof":
        if not (
            self.resume_token_verified
            and self.effect_fence_verified
            and self.checkpoint_consumed
        ):
            raise ValueError("resume proof requires all recovery guards")
        return self
