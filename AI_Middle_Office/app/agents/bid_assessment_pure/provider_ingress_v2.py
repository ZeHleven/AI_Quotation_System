"""V2-A contracts for one fail-closed Provider JSON ingress boundary.

This module deliberately contains no JSON repair implementation and performs no
I/O.  It defines the metadata, receipts, typed failures, and port shared by
assistant JSON, structured output, and Function Call arguments.  Raw provider
text is passed separately to the port so it cannot be serialized into a durable
contract, diagnostic snapshot, or exception by accident.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from .common import Reference, StrictContract, ToolName
from .tool_runtime import canonical_hash, canonical_json


def raw_text_hash(value: str) -> str:
    """Hash exact Provider UTF-8 bytes without JSON canonicalization."""

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class ProviderIngressPayloadKind(str, Enum):
    ASSISTANT_JSON = "assistant_json"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_ARGUMENTS = "tool_arguments"


class ProviderIngressNormalizationStep(str, Enum):
    MARKDOWN_FENCE_REMOVED = "markdown_fence_removed"
    SINGLE_JSON_OBJECT_EXTRACTED = "single_json_object_extracted"
    ADVISORY_SOURCE_HINTS_FILTERED = "advisory_source_hints_filtered"


class ProviderBoundaryFailureStage(str, Enum):
    INGRESS = "ingress"
    NEXT_ACTION = "next_action"
    ANSWER_PROJECTION = "answer_projection"
    RUNTIME_BINDING = "runtime_binding"


class ProviderBoundaryFailureCode(str, Enum):
    BOUNDARY_DISABLED = "boundary_disabled"
    JSON_SIZE_LIMIT = "json_size_limit"
    JSON_ENCODING_INVALID = "json_encoding_invalid"
    JSON_ENVELOPE_INVALID = "json_envelope_invalid"
    JSON_DUPLICATE_KEY = "json_duplicate_key"
    JSON_MULTIPLE_OBJECTS = "json_multiple_objects"
    JSON_TRUNCATED = "json_truncated"
    JSON_NON_OBJECT = "json_non_object"
    TOOL_ARGUMENTS_INVALID = "tool_arguments_invalid"
    TOOL_NOT_VISIBLE = "tool_not_visible"
    CONTEXT_NOT_MODEL_READY = "context_not_model_ready"
    DECISION_SCHEMA_INVALID = "decision_schema_invalid"
    LOCKED_ACTION_PAYLOAD_INVALID = "locked_action_payload_invalid"
    ANSWER_SCHEMA_INVALID = "answer_schema_invalid"
    ANSWER_GROUNDING_REJECTED = "answer_grounding_rejected"
    RUNTIME_BINDING_INVALID = "runtime_binding_invalid"


class ProviderValidationIssue(StrictContract):
    """Redacted schema issue containing no message, input value, or Provider text."""

    path: str = Field(
        min_length=1,
        max_length=240,
        pattern=r"^\$[A-Za-z0-9_.\[\]-]*$",
    )
    error_type: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9_.-]+$",
    )

    @property
    def diagnostic_code(self) -> str:
        normalized_path = self.path.removeprefix("$.").removeprefix("$") or "root"
        normalized_path = normalized_path.replace("[", ".").replace("]", "")
        return f"provider_validation.{normalized_path}.{self.error_type}"[:240]


class ProviderBoundaryFailure(StrictContract):
    """Safe failure metadata; never contains raw provider content."""

    stage: ProviderBoundaryFailureStage
    code: ProviderBoundaryFailureCode
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    structurally_repairable: bool = Field(
        default=False,
        description=(
            "Legacy wire name. True when this Provider projection failure is "
            "eligible for, or records, one bounded Runtime recovery."
        ),
    )
    repair_attempt: int = Field(default=0, ge=0, le=1)
    validation_issues: tuple[ProviderValidationIssue, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_repair_shape(self) -> "ProviderBoundaryFailure":
        if self.repair_attempt and not self.structurally_repairable:
            raise ValueError("repair attempt requires a bounded-repairable failure")
        if self.validation_issues and not self.structurally_repairable:
            raise ValueError(
                "validation issues require a bounded-repairable failure"
            )
        issue_keys = tuple(
            (issue.path, issue.error_type) for issue in self.validation_issues
        )
        if len(issue_keys) != len(set(issue_keys)):
            raise ValueError("validation issues must be unique")
        return self


class ProviderBoundaryRejected(RuntimeError):
    """One typed Provider boundary failure safe for Runtime settlement."""

    def __init__(self, failure: ProviderBoundaryFailure) -> None:
        super().__init__(failure.safe_message)
        self.failure = failure


class ProviderBoundaryV2Config(StrictContract):
    """Default-off limits for the explicitly selected isolated V2 boundary."""

    enabled: bool = False
    max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=4 * 1024 * 1024)
    max_tool_arguments_bytes: int = Field(default=16 * 1024, ge=256, le=256 * 1024)
    max_structural_repair_attempts: Literal[1] = 1
    allow_markdown_fence_removal: bool = True
    allow_single_object_extraction: bool = True

    @model_validator(mode="after")
    def validate_limits(self) -> "ProviderBoundaryV2Config":
        if self.max_tool_arguments_bytes > self.max_response_bytes:
            raise ValueError("Tool argument limit cannot exceed response limit")
        return self


class ProviderIngressRequest(StrictContract):
    """Hash-bound request metadata; raw provider text is intentionally absent."""

    request_ref: Reference
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    call_ref: Reference
    payload_kind: ProviderIngressPayloadKind
    expected_contract_ref: Reference
    tool_name: ToolName | None = None
    raw_payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    raw_size_bytes: int = Field(ge=0)
    max_size_bytes: int = Field(ge=1, le=4 * 1024 * 1024)

    @classmethod
    def from_raw(
        cls,
        *,
        call_ref: str,
        payload_kind: ProviderIngressPayloadKind,
        expected_contract_ref: str,
        raw_value: str,
        max_size_bytes: int,
        tool_name: str | None = None,
    ) -> "ProviderIngressRequest":
        body = {
            "call_ref": call_ref,
            "payload_kind": payload_kind,
            "expected_contract_ref": expected_contract_ref,
            "tool_name": tool_name,
            "raw_payload_hash": raw_text_hash(raw_value),
            "raw_size_bytes": len(raw_value.encode("utf-8")),
            "max_size_bytes": max_size_bytes,
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            request_ref=f"provider-ingress-request:{digest.removeprefix('sha256:')}",
            request_hash=digest,
        )

    @model_validator(mode="after")
    def validate_request(self) -> "ProviderIngressRequest":
        body = self.model_dump(mode="json", exclude={"request_ref", "request_hash"})
        digest = canonical_hash(body)
        if self.request_hash != digest:
            raise ValueError("request_hash does not match Provider ingress request")
        if self.request_ref != (
            f"provider-ingress-request:{digest.removeprefix('sha256:')}"
        ):
            raise ValueError("request_ref does not match Provider ingress request")
        if (self.payload_kind is ProviderIngressPayloadKind.TOOL_ARGUMENTS) != (
            self.tool_name is not None
        ):
            raise ValueError("tool_name must appear exactly for Tool arguments")
        return self


class ProviderIngressReceipt(StrictContract):
    """Durable-safe evidence of lossless ingress and audited projection."""

    receipt_ref: Reference
    receipt_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_ref: Reference
    payload_kind: ProviderIngressPayloadKind
    normalized_payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validated_contract_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    normalized_size_bytes: int = Field(ge=2, le=4 * 1024 * 1024)
    normalization_steps: tuple[ProviderIngressNormalizationStep, ...] = Field(
        default_factory=tuple,
        max_length=3,
    )
    exact_json_value_preserved: Literal[True] = True
    schema_validated: bool = False

    @classmethod
    def build(
        cls,
        *,
        request: ProviderIngressRequest,
        normalized_payload: dict[str, Any],
        normalization_steps: tuple[ProviderIngressNormalizationStep, ...] = (),
        schema_validated: bool = False,
        validated_contract: Any | None = None,
    ) -> "ProviderIngressReceipt":
        serialized = canonical_json(normalized_payload)
        body = {
            "request_ref": request.request_ref,
            "payload_kind": request.payload_kind,
            "normalized_payload_hash": canonical_hash(normalized_payload),
            "validated_contract_hash": (
                canonical_hash(validated_contract)
                if validated_contract is not None
                else None
            ),
            "normalized_size_bytes": len(serialized.encode("utf-8")),
            "normalization_steps": normalization_steps,
            "exact_json_value_preserved": True,
            "schema_validated": schema_validated,
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            receipt_ref=f"provider-ingress-receipt:{digest.removeprefix('sha256:')}",
            receipt_hash=digest,
        )

    @model_validator(mode="after")
    def validate_receipt(self) -> "ProviderIngressReceipt":
        if len(self.normalization_steps) != len(set(self.normalization_steps)):
            raise ValueError("normalization_steps must be unique")
        if self.schema_validated != (self.validated_contract_hash is not None):
            raise ValueError(
                "schema validation and validated contract hash must appear together"
            )
        body = self.model_dump(mode="json", exclude={"receipt_ref", "receipt_hash"})
        digest = canonical_hash(body)
        if self.receipt_hash != digest:
            raise ValueError("receipt_hash does not match Provider ingress receipt")
        if self.receipt_ref != (
            f"provider-ingress-receipt:{digest.removeprefix('sha256:')}"
        ):
            raise ValueError("receipt_ref does not match Provider ingress receipt")
        return self


class ProviderIngressResult(StrictContract):
    request_ref: Reference
    payload: dict[str, Any]
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    receipt: ProviderIngressReceipt

    @model_validator(mode="after")
    def validate_result(self) -> "ProviderIngressResult":
        digest = canonical_hash(self.payload)
        if self.payload_hash != digest:
            raise ValueError("payload_hash does not match normalized payload")
        if self.receipt.request_ref != self.request_ref:
            raise ValueError("Provider ingress receipt belongs to another request")
        if self.receipt.normalized_payload_hash != digest:
            raise ValueError("Provider ingress receipt payload hash drifted")
        return self


class ProviderJsonIngressPort(Protocol):
    """Transient raw text in; validated JSON Object plus safe receipt out."""

    def normalize(
        self,
        *,
        request: ProviderIngressRequest,
        raw_value: str,
    ) -> ProviderIngressResult: ...
