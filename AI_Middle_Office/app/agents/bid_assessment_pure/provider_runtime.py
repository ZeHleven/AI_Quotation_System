"""Fail-closed Provider Adapter and structured-call bridge for B04-3.

The module contains contracts, deterministic serialization, schema capability
negotiation, Function Calling normalization, and disabled/static seams.  It has
no API key, endpoint, SDK client, network side effect, or fixed Agent call order.
"""

from __future__ import annotations

from enum import Enum
import json
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError, model_validator

from .common import Reference, StrictContentContract, StrictContract, ToolName
from .runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
    ContextTrustClass,
    ToolCallRequest,
)
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import RegistrySnapshot, canonical_hash, canonical_json


_DEFAULT_SCHEMA_KEYWORDS = (
    "$defs",
    "$ref",
    "additionalProperties",
    "anyOf",
    "const",
    "default",
    "description",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "multipleOf",
    "oneOf",
    "pattern",
    "prefixItems",
    "properties",
    "required",
    "title",
    "type",
)


def _json_ready(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


class ProviderStrictMode(str, Enum):
    DISABLED = "disabled"
    PREFERRED = "preferred"
    REQUIRED = "required"


class ProviderToolChoice(str, Enum):
    NONE = "none"
    AUTO = "auto"
    REQUIRED = "required"


class ProviderOutputKind(str, Enum):
    TEXT = "text"
    STRUCTURED = "structured"
    TOOL_CALLS = "tool_calls"


class ProviderErrorCode(str, Enum):
    DISABLED = "disabled"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"
    CONTEXT_REJECTED = "context_rejected"
    SERIALIZATION_FAILED = "serialization_failed"
    INPUT_LIMIT_EXCEEDED = "input_limit_exceeded"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_FAILED = "authentication_failed"
    PROVIDER_REJECTED = "provider_rejected"
    TOOL_CALL_LIMIT_EXCEEDED = "tool_call_limit_exceeded"
    TOOL_NAME_NOT_VISIBLE = "tool_name_not_visible"
    RESPONSE_JSON_ENVELOPE_INVALID = "response_json_envelope_invalid"
    RESPONSE_JSON_SIZE_LIMIT = "response_json_size_limit"
    RESPONSE_CONTRACT_VIOLATION = "response_contract_violation"
    CANCELLED = "cancelled"


class ProviderFailure(StrictContract):
    code: ProviderErrorCode
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    provider_receipt_ref: Reference | None = None
    response_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_structured_json_failure(self) -> "ProviderFailure":
        structured_json_codes = {
            ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID,
            ProviderErrorCode.RESPONSE_JSON_SIZE_LIMIT,
        }
        if self.code in structured_json_codes and (
            self.provider_receipt_ref is None or self.response_hash is None
        ):
            raise ValueError(
                "structured JSON failure requires a safe response receipt and hash"
            )
        if (
            self.code is ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID
            and not self.retryable
        ):
            raise ValueError("invalid structured JSON envelope must be retryable")
        if (
            self.code is ProviderErrorCode.RESPONSE_JSON_SIZE_LIMIT
            and self.retryable
        ):
            raise ValueError("structured JSON size limit cannot be retryable")
        return self


class ProviderAdapterError(RuntimeError):
    """Only exposes a bounded safe failure, never a raw provider response."""

    def __init__(self, failure: ProviderFailure):
        self.failure = failure
        super().__init__(failure.safe_message)


class ProviderTransportFailure(RuntimeError):
    """Safe error raised by an injected transport implementation."""

    def __init__(self, failure: ProviderFailure):
        self.failure = failure
        super().__init__(failure.safe_message)


class ProviderCodecError(ValueError):
    """Internal codec signal whose raw cause is never exposed to callers."""


class ProviderJsonObjectFailureKind(str, Enum):
    ENCODING_INVALID = "encoding_invalid"
    SIZE_LIMIT = "size_limit"
    DUPLICATE_KEY = "duplicate_key"
    NON_FINITE_NUMBER = "non_finite_number"
    INVALID_JSON = "invalid_json"
    NON_OBJECT = "non_object"


class ProviderJsonObjectError(ProviderCodecError):
    """Typed JSON-object parser signal containing no rejected content."""

    def __init__(
        self,
        *,
        kind: ProviderJsonObjectFailureKind,
        safe_message: str,
    ) -> None:
        self.kind = kind
        super().__init__(safe_message)


class ProviderStructuredJsonEnvelopeError(ProviderCodecError):
    """Safe structured-response JSON signal raised after HTTP envelope decoding."""

    def __init__(
        self,
        *,
        kind: ProviderJsonObjectFailureKind,
        provider_receipt_ref: Reference,
        response_hash: str,
    ) -> None:
        self.kind = kind
        self.provider_receipt_ref = provider_receipt_ref
        self.response_hash = response_hash
        super().__init__("provider structured response JSON envelope is invalid")


class ProviderToolCallLimitExceeded(ProviderCodecError):
    """Typed, content-free signal for a Provider Tool Call count overflow."""

    def __init__(self, *, actual_count: int, limit: int) -> None:
        self.actual_count = actual_count
        self.limit = limit
        super().__init__(
            f"provider returned {actual_count} Tool Calls; limit is {limit}"
        )


class ProviderToolNameNotVisible(ProviderCodecError):
    """Content-free signal for a Tool name outside the visible Registry."""

    def __init__(self) -> None:
        super().__init__("provider selected a Tool outside the visible Registry")


class ProviderCapabilities(StrictContract):
    capability_ref: Reference
    capability_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    enabled: bool = False
    provider_ref: Reference
    model_ref: Reference
    model_profile_ref: Reference
    model_profile_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    codec_ref: Reference
    token_counter_ref: Reference
    supports_function_calling: bool = False
    supports_strict_tools: bool = False
    supports_structured_output: bool = False
    supports_strict_structured_output: bool = False
    supports_parallel_tool_calls: bool = False
    supports_tool_calls_with_structured_output: bool = False
    supported_json_schema_keywords: tuple[str, ...] = Field(
        default=_DEFAULT_SCHEMA_KEYWORDS,
        min_length=1,
        max_length=128,
    )
    max_visible_tools: int = Field(default=32, ge=1, le=128)
    max_tool_calls_per_response: int = Field(default=16, ge=1, le=64)
    max_arguments_bytes: int = Field(default=16 * 1024, ge=1024, le=1024 * 1024)
    max_response_bytes: int = Field(default=1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    max_output_tokens: int = Field(default=8192, ge=1, le=1_000_000)

    @classmethod
    def build(cls, **values: Any) -> "ProviderCapabilities":
        body = dict(values)
        body.setdefault("supported_json_schema_keywords", _DEFAULT_SCHEMA_KEYWORDS)
        body.setdefault("max_visible_tools", 32)
        body.setdefault("max_tool_calls_per_response", 16)
        body.setdefault("max_arguments_bytes", 16 * 1024)
        body.setdefault("max_response_bytes", 1024 * 1024)
        body.setdefault("max_output_tokens", 8192)
        body.setdefault("enabled", False)
        body.setdefault("supports_function_calling", False)
        body.setdefault("supports_strict_tools", False)
        body.setdefault("supports_structured_output", False)
        body.setdefault("supports_strict_structured_output", False)
        body.setdefault("supports_parallel_tool_calls", False)
        body.setdefault("supports_tool_calls_with_structured_output", False)
        return cls(capability_hash=canonical_hash(body), **body)

    @classmethod
    def disabled(cls) -> "ProviderCapabilities":
        return cls.build(
            capability_ref="provider-capability:disabled",
            provider_ref="provider:disabled",
            model_ref="model:disabled",
            model_profile_ref="model-profile:disabled",
            model_profile_hash=canonical_hash({"enabled": False}),
            codec_ref="provider-codec:openai-compatible-v1",
            token_counter_ref="provider-token-counter:disabled",
        )

    @model_validator(mode="after")
    def validate_capability(self) -> "ProviderCapabilities":
        body = self.model_dump(mode="json", exclude={"capability_hash"})
        if self.capability_hash != canonical_hash(body):
            raise ValueError("capability_hash does not match capability content")
        if len(self.supported_json_schema_keywords) != len(
            set(self.supported_json_schema_keywords)
        ):
            raise ValueError("supported_json_schema_keywords must be unique")
        if self.supports_strict_tools and not self.supports_function_calling:
            raise ValueError("strict tools require Function Calling support")
        if (
            self.supports_strict_structured_output
            and not self.supports_structured_output
        ):
            raise ValueError("strict structured output requires structured output")
        if self.supports_parallel_tool_calls and not self.supports_function_calling:
            raise ValueError("parallel Tool Calls require Function Calling support")
        if self.supports_tool_calls_with_structured_output and not (
            self.supports_function_calling and self.supports_structured_output
        ):
            raise ValueError(
                "combined Tool Calls and structured output require both capabilities"
            )
        if not self.enabled and any(
            (
                self.supports_function_calling,
                self.supports_strict_tools,
                self.supports_structured_output,
                self.supports_strict_structured_output,
                self.supports_parallel_tool_calls,
                self.supports_tool_calls_with_structured_output,
            )
        ):
            raise ValueError("disabled provider capabilities cannot advertise support")
        return self


class ProviderSchemaProjection(StrictContract):
    projected_schema: dict[str, Any]
    schema_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_compatible: bool
    strict_compatible: bool
    strict_enabled: bool
    issues: tuple[str, ...] = Field(default_factory=tuple, max_length=128)

    @model_validator(mode="after")
    def validate_projection(self) -> "ProviderSchemaProjection":
        if self.schema_hash != canonical_hash(self.projected_schema):
            raise ValueError("schema_hash does not match projected schema")
        if self.strict_enabled and (
            not self.provider_compatible or not self.strict_compatible
        ):
            raise ValueError("strict cannot be enabled for an incompatible schema")
        return self


class ProviderSchemaProjector:
    """Validate a Pydantic JSON Schema against a frozen Provider capability."""

    def project(
        self,
        schema: Mapping[str, Any],
        *,
        capabilities: ProviderCapabilities,
        strict_mode: ProviderStrictMode,
        strict_supported: bool,
    ) -> ProviderSchemaProjection:
        try:
            projected = json.loads(canonical_json(dict(schema)))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderCodecError("schema is not canonical JSON") from exc
        projected = self._without_openapi_discriminator(projected)
        issues: list[str] = []
        if projected.get("type") != "object":
            issues.append("root_schema_must_be_object")
        self._collect_keyword_issues(
            projected,
            path="$",
            supported=set(capabilities.supported_json_schema_keywords),
            issues=issues,
        )
        provider_issues = tuple(dict.fromkeys(issues))
        strict_issues: list[str] = []
        self._collect_strict_issues(projected, path="$", issues=strict_issues)
        strict_compatible = not strict_issues
        strict_enabled = (
            strict_mode is not ProviderStrictMode.DISABLED
            and strict_supported
            and not provider_issues
            and strict_compatible
        )
        all_issues = list(provider_issues)
        if strict_mode is not ProviderStrictMode.DISABLED:
            if not strict_supported:
                all_issues.append("provider_strict_not_supported")
            all_issues.extend(strict_issues)
        return ProviderSchemaProjection(
            projected_schema=projected,
            schema_hash=canonical_hash(projected),
            provider_compatible=not provider_issues,
            strict_compatible=strict_compatible,
            strict_enabled=strict_enabled,
            issues=tuple(dict.fromkeys(all_issues)),
        )

    @classmethod
    def _without_openapi_discriminator(cls, schema: Any) -> Any:
        """Keep oneOf/const while dropping a non-authoritative OpenAPI hint.

        Pydantic uses ``discriminator`` to optimize its own tagged-union
        validation. The Provider JSON Schema subset does not need that hint:
        each alternative still carries the model-visible tag as a const, and
        the original Pydantic model remains the final Runtime authority.
        """

        if isinstance(schema, dict):
            return {
                key: cls._without_openapi_discriminator(value)
                for key, value in schema.items()
                if key != "discriminator"
            }
        if isinstance(schema, list):
            return [cls._without_openapi_discriminator(value) for value in schema]
        return schema

    def _collect_keyword_issues(
        self,
        schema: Any,
        *,
        path: str,
        supported: set[str],
        issues: list[str],
    ) -> None:
        if not isinstance(schema, dict):
            return
        for key, value in schema.items():
            if key not in supported:
                issues.append(f"unsupported_keyword:{path}.{key}")
            if key in {"properties", "$defs"} and isinstance(value, dict):
                for child_name, child_schema in value.items():
                    self._collect_keyword_issues(
                        child_schema,
                        path=f"{path}.{key}.{child_name}",
                        supported=supported,
                        issues=issues,
                    )
            elif key in {"anyOf", "oneOf", "prefixItems"} and isinstance(value, list):
                for index, child_schema in enumerate(value):
                    self._collect_keyword_issues(
                        child_schema,
                        path=f"{path}.{key}[{index}]",
                        supported=supported,
                        issues=issues,
                    )
            elif key in {"items", "additionalProperties"} and isinstance(value, dict):
                self._collect_keyword_issues(
                    value,
                    path=f"{path}.{key}",
                    supported=supported,
                    issues=issues,
                )

    def _collect_strict_issues(
        self,
        schema: Any,
        *,
        path: str,
        issues: list[str],
    ) -> None:
        if not isinstance(schema, dict):
            return
        if schema.get("type") == "object" or "properties" in schema:
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if schema.get("additionalProperties") is not False:
                issues.append(f"strict_object_allows_extra:{path}")
            if isinstance(properties, dict) and set(required) != set(properties):
                issues.append(f"strict_object_has_optional_fields:{path}")
        if "default" in schema:
            issues.append(f"strict_schema_uses_default:{path}")
        for key in ("properties", "$defs"):
            value = schema.get(key)
            if isinstance(value, dict):
                for child_name, child_schema in value.items():
                    self._collect_strict_issues(
                        child_schema,
                        path=f"{path}.{key}.{child_name}",
                        issues=issues,
                    )
        for key in ("anyOf", "oneOf", "prefixItems"):
            value = schema.get(key)
            if isinstance(value, list):
                for index, child_schema in enumerate(value):
                    self._collect_strict_issues(
                        child_schema,
                        path=f"{path}.{key}[{index}]",
                        issues=issues,
                    )
        for key in ("items", "additionalProperties"):
            value = schema.get(key)
            if isinstance(value, dict):
                self._collect_strict_issues(
                    value,
                    path=f"{path}.{key}",
                    issues=issues,
                )


class ProviderStructuredOutputSpec(StrictContract):
    schema_name: ToolName
    output_schema: dict[str, Any]
    output_schema_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strict_mode: ProviderStrictMode = ProviderStrictMode.PREFERRED

    @classmethod
    def from_model(
        cls,
        *,
        schema_name: str,
        output_model: type[BaseModel],
        strict_mode: ProviderStrictMode = ProviderStrictMode.PREFERRED,
    ) -> "ProviderStructuredOutputSpec":
        schema = output_model.model_json_schema()
        return cls(
            schema_name=schema_name,
            output_schema=schema,
            output_schema_hash=canonical_hash(schema),
            strict_mode=strict_mode,
        )

    @model_validator(mode="after")
    def validate_schema_hash(self) -> "ProviderStructuredOutputSpec":
        if self.output_schema_hash != canonical_hash(self.output_schema):
            raise ValueError("output_schema_hash does not match output_schema")
        return self


class ProviderRuntimeInput(StrictContract):
    input_ref: Reference
    input_kind: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any]
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def from_payload(
        cls,
        *,
        input_ref: str,
        input_kind: str,
        payload: Mapping[str, Any],
    ) -> "ProviderRuntimeInput":
        value = dict(payload)
        return cls(
            input_ref=input_ref,
            input_kind=input_kind,
            payload=value,
            payload_hash=canonical_hash(value),
        )

    @model_validator(mode="after")
    def validate_payload_hash(self) -> "ProviderRuntimeInput":
        if self.payload_hash != canonical_hash(self.payload):
            raise ValueError("payload_hash does not match runtime input")
        return self


class ProviderMessageToolCall(StrictContentContract):
    tool_call_id: Reference
    name: ToolName
    arguments_json: str = Field(min_length=2, max_length=1024 * 1024)
    raw_arguments_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_arguments_hash(self) -> "ProviderMessageToolCall":
        if self.raw_arguments_hash != canonical_hash(self.arguments_json):
            raise ValueError("raw_arguments_hash does not match arguments_json")
        return self


class ProviderMessage(StrictContentContract):
    role: str = Field(pattern=r"^(system|user|assistant|tool)$")
    content: str | None = Field(default=None, min_length=1, max_length=131_072)
    tool_calls: tuple[ProviderMessageToolCall, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    tool_call_id: Reference | None = None
    name: ToolName | None = None
    source_entry_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)

    @model_validator(mode="after")
    def validate_role_shape(self) -> "ProviderMessage":
        if self.role in {"system", "user"}:
            if self.content is None or self.tool_calls or self.tool_call_id or self.name:
                raise ValueError("system/user messages only carry content")
        elif self.role == "assistant":
            if self.tool_call_id or self.name:
                raise ValueError("assistant messages cannot carry Tool result metadata")
            if self.content is None and not self.tool_calls:
                raise ValueError("assistant message requires content or Tool Calls")
        else:
            if self.content is None or self.tool_call_id is None or self.name is None:
                raise ValueError("tool message requires content, call id, and name")
            if self.tool_calls:
                raise ValueError("tool message cannot contain Tool Calls")
        if len(self.source_entry_refs) != len(set(self.source_entry_refs)):
            raise ValueError("source_entry_refs must be unique")
        return self


class ProviderFunctionDefinition(StrictContract):
    name: ToolName
    description: str = Field(min_length=1, max_length=1000)
    parameters: dict[str, Any]
    parameters_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strict_enabled: bool
    projection_issues: tuple[str, ...] = Field(default_factory=tuple, max_length=128)

    @model_validator(mode="after")
    def validate_parameters_hash(self) -> "ProviderFunctionDefinition":
        if self.parameters_hash != canonical_hash(self.parameters):
            raise ValueError("parameters_hash does not match parameters")
        return self


class ProviderStructuredOutputProjection(StrictContract):
    schema_name: ToolName
    output_schema: dict[str, Any]
    schema_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strict_enabled: bool
    projection_issues: tuple[str, ...] = Field(default_factory=tuple, max_length=128)

    @model_validator(mode="after")
    def validate_output_schema_hash(self) -> "ProviderStructuredOutputProjection":
        if self.schema_hash != canonical_hash(self.output_schema):
            raise ValueError("schema_hash does not match structured output schema")
        return self


class ProviderInvocationRequest(StrictContract):
    call_ref: Reference
    task_ref: Reference
    state_version: int = Field(ge=1)
    consumer: ContextConsumer
    context: ContextAssemblyResult
    registry_snapshot: RegistrySnapshot | None = None
    tool_name_filter: tuple[ToolName, ...] | None = Field(
        default=None,
        max_length=32,
    )
    runtime_input: ProviderRuntimeInput | None = None
    structured_output: ProviderStructuredOutputSpec | None = None
    tool_choice: ProviderToolChoice = ProviderToolChoice.NONE
    tool_strict_mode: ProviderStrictMode = ProviderStrictMode.PREFERRED
    max_output_tokens: int = Field(ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_binding(self) -> "ProviderInvocationRequest":
        snapshot = self.context.snapshot
        if (
            snapshot.task_ref != self.task_ref
            or snapshot.state_version != self.state_version
            or snapshot.consumer is not self.consumer
        ):
            raise ValueError("model invocation does not match its Context Snapshot")
        if snapshot.status not in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            raise ValueError("model invocation requires model-ready Context")
        if self.max_output_tokens > snapshot.reserved_output_tokens:
            raise ValueError("model output budget exceeds the Context reservation")
        if self.registry_snapshot is None:
            if snapshot.registry_snapshot_ref is not None:
                raise ValueError("registry snapshot required by Context is missing")
            if self.tool_choice is not ProviderToolChoice.NONE:
                raise ValueError("Tool choice requires a registry snapshot")
            if self.tool_name_filter is not None:
                raise ValueError("Tool filter requires a registry snapshot")
        else:
            if (
                snapshot.registry_snapshot_ref != self.registry_snapshot.snapshot_ref
                or snapshot.registry_snapshot_hash != self.registry_snapshot.snapshot_hash
            ):
                raise ValueError("registry snapshot does not match Context Snapshot")
            if self.tool_name_filter is not None:
                if len(self.tool_name_filter) != len(set(self.tool_name_filter)):
                    raise ValueError("Tool filter names must be unique")
                if not set(self.tool_name_filter).issubset(
                    self.registry_snapshot.visible_tool_names
                ):
                    raise ValueError("Tool filter exceeds the visible Registry")
                if (
                    self.tool_choice is not ProviderToolChoice.NONE
                    and not self.tool_name_filter
                ):
                    raise ValueError("Tool choice requires a non-empty Tool filter")
        return self


class ProviderRenderedRequest(StrictContract):
    call_ref: Reference
    task_ref: Reference
    state_version: int = Field(ge=1)
    consumer: ContextConsumer
    context_snapshot_ref: Reference
    context_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_ref: Reference
    model_ref: Reference
    capability_ref: Reference
    capability_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    messages: tuple[ProviderMessage, ...] = Field(min_length=1, max_length=1000)
    tools: tuple[ProviderFunctionDefinition, ...] = Field(default_factory=tuple, max_length=128)
    structured_output: ProviderStructuredOutputProjection | None = None
    tool_choice: ProviderToolChoice
    parallel_tool_calls: bool = False
    max_output_tokens: int = Field(ge=1, le=1_000_000)
    input_token_limit: int = Field(ge=1)
    assembled_estimated_input_tokens: int = Field(ge=0)
    registry_snapshot_ref: Reference | None = None
    registry_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    visible_tools_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_rendered_request(self) -> "ProviderRenderedRequest":
        body = self.model_dump(mode="json", exclude={"request_hash"})
        if self.request_hash != canonical_hash(body):
            raise ValueError("request_hash does not match rendered request")
        if len(self.tools) != len({tool.name for tool in self.tools}):
            raise ValueError("rendered Tool names must be unique")
        if not self.tools and self.tool_choice is not ProviderToolChoice.NONE:
            raise ValueError("Tool choice requires rendered tools")
        if (self.registry_snapshot_ref is None) != (
            self.registry_snapshot_hash is None
        ) or (self.registry_snapshot_ref is None) != (self.visible_tools_hash is None):
            raise ValueError("registry ref, hash, and visible hash must appear together")
        return self


class ProviderWireRequest(StrictContract):
    call_ref: Reference
    payload: dict[str, Any]
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    serialized_input_tokens: int = Field(ge=0)
    input_token_limit: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_wire_request(self) -> "ProviderWireRequest":
        if self.payload_hash != canonical_hash(self.payload):
            raise ValueError("payload_hash does not match wire payload")
        if self.serialized_input_tokens > self.input_token_limit:
            raise ValueError("wire request exceeds input token limit")
        return self


class ProviderUsage(StrictContract):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_verified: bool = False

    @model_validator(mode="after")
    def validate_totals(self) -> "ProviderUsage":
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("provider usage total is inconsistent")
        return self


class ProviderRawToolCall(StrictContentContract):
    tool_call_id: Reference
    name: ToolName
    raw_arguments_json: str = Field(min_length=2, max_length=1024 * 1024)


class ProviderDecodedResponse(StrictContentContract):
    call_ref: Reference
    finish_reason: str = Field(min_length=1, max_length=80)
    assistant_text: str | None = Field(default=None, min_length=1, max_length=131_072)
    structured_payload: dict[str, Any] | None = None
    tool_calls: tuple[ProviderRawToolCall, ...] = Field(default_factory=tuple, max_length=64)
    usage: ProviderUsage
    provider_receipt_ref: Reference

    @model_validator(mode="after")
    def validate_outcome(self) -> "ProviderDecodedResponse":
        if self.structured_payload is not None and (
            self.assistant_text is not None or self.tool_calls
        ):
            raise ValueError("structured response cannot mix text or Tool Calls")
        if self.structured_payload is None and not self.tool_calls and self.assistant_text is None:
            raise ValueError("provider response has no usable output")
        if len(self.tool_calls) != len({call.tool_call_id for call in self.tool_calls}):
            raise ValueError("provider Tool Call ids must be unique")
        return self


class ProviderToolCallProposal(StrictContentContract):
    model_turn_ref: Reference
    provider_tool_call_id: Reference
    sequence: int = Field(ge=1, le=64)
    task_ref: Reference
    context_snapshot_ref: Reference
    state_version: int = Field(ge=1)
    tool_name: ToolName
    raw_arguments_json: str = Field(min_length=2, max_length=1024 * 1024)
    raw_arguments_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    arguments: dict[str, Any]
    arguments_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    registry_snapshot_ref: Reference
    registry_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    visible_tools_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authorization_snapshot_ref: Reference

    @model_validator(mode="after")
    def validate_argument_hashes(self) -> "ProviderToolCallProposal":
        if self.raw_arguments_hash != canonical_hash(self.raw_arguments_json):
            raise ValueError("raw_arguments_hash does not match raw arguments")
        if self.arguments_hash != canonical_hash(self.arguments):
            raise ValueError("arguments_hash does not match normalized arguments")
        return self


class ProviderModelResult(StrictContentContract):
    result_ref: Reference
    call_ref: Reference
    task_ref: Reference
    state_version: int = Field(ge=1)
    consumer: ContextConsumer
    context_snapshot_ref: Reference
    output_kind: ProviderOutputKind
    assistant_text: str | None = Field(default=None, min_length=1, max_length=131_072)
    structured_payload: dict[str, Any] | None = None
    tool_call_proposals: tuple[ProviderToolCallProposal, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    finish_reason: str = Field(min_length=1, max_length=80)
    usage: ProviderUsage
    serialized_input_tokens: int = Field(ge=0)
    provider_receipt_ref: Reference
    response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_output_kind(self) -> "ProviderModelResult":
        if self.output_kind is ProviderOutputKind.TEXT:
            if self.assistant_text is None or self.structured_payload is not None or self.tool_call_proposals:
                raise ValueError("text result shape is invalid")
        elif self.output_kind is ProviderOutputKind.STRUCTURED:
            if self.structured_payload is None or self.assistant_text is not None or self.tool_call_proposals:
                raise ValueError("structured result shape is invalid")
        elif not self.tool_call_proposals or self.structured_payload is not None:
            raise ValueError("Tool Call result shape is invalid")
        return self


def parse_json_object(raw: str, *, max_bytes: int) -> dict[str, Any]:
    try:
        encoded = raw.encode("utf-8")
    except UnicodeError as exc:
        raise ProviderJsonObjectError(
            kind=ProviderJsonObjectFailureKind.ENCODING_INVALID,
            safe_message="JSON object encoding is invalid",
        ) from exc
    if len(encoded) > max_bytes:
        raise ProviderJsonObjectError(
            kind=ProviderJsonObjectFailureKind.SIZE_LIMIT,
            safe_message="JSON object exceeds the configured size limit",
        )

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProviderJsonObjectError(
                    kind=ProviderJsonObjectFailureKind.DUPLICATE_KEY,
                    safe_message="JSON object contains duplicate keys",
                )
            result[key] = value
        return result

    def reject_constant(_: str) -> Any:
        raise ProviderJsonObjectError(
            kind=ProviderJsonObjectFailureKind.NON_FINITE_NUMBER,
            safe_message="non-finite JSON number is not allowed",
        )

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderJsonObjectError(
            kind=ProviderJsonObjectFailureKind.INVALID_JSON,
            safe_message="value is not valid JSON",
        ) from exc
    if not isinstance(value, dict):
        raise ProviderJsonObjectError(
            kind=ProviderJsonObjectFailureKind.NON_OBJECT,
            safe_message="value must be a JSON Object",
        )
    return value


class ProviderRequestCodec(Protocol):
    codec_ref: str

    def encode(self, request: ProviderRenderedRequest) -> dict[str, Any]: ...

    def decode(
        self,
        *,
        request: ProviderRenderedRequest,
        payload: Mapping[str, Any],
        max_response_bytes: int,
    ) -> ProviderDecodedResponse: ...


class OpenAICompatibleChatCodec:
    """Pure serializer/parser for an OpenAI-compatible Chat Completions shape."""

    codec_ref = "provider-codec:openai-compatible-v1"

    def encode(self, request: ProviderRenderedRequest) -> dict[str, Any]:
        messages = [self._encode_message(message) for message in request.messages]
        payload: dict[str, Any] = {
            "model": request.model_ref,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        **({"strict": True} if tool.strict_enabled else {}),
                    },
                }
                for tool in request.tools
            ]
            payload["tool_choice"] = request.tool_choice.value
            payload["parallel_tool_calls"] = request.parallel_tool_calls
        if request.structured_output is not None:
            projection = request.structured_output
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": projection.schema_name,
                    "schema": projection.output_schema,
                    "strict": projection.strict_enabled,
                },
            }
        return payload

    def decode(
        self,
        *,
        request: ProviderRenderedRequest,
        payload: Mapping[str, Any],
        max_response_bytes: int,
    ) -> ProviderDecodedResponse:
        try:
            encoded = canonical_json(dict(payload)).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProviderCodecError("provider response is not canonical JSON") from exc
        response_hash = canonical_hash(dict(payload))
        provider_receipt_ref = (
            "provider-response:" + response_hash.removeprefix("sha256:")
        )
        if len(encoded) > max_response_bytes:
            if request.structured_output is not None:
                raise ProviderStructuredJsonEnvelopeError(
                    kind=ProviderJsonObjectFailureKind.SIZE_LIMIT,
                    provider_receipt_ref=provider_receipt_ref,
                    response_hash=response_hash,
                )
            raise ProviderCodecError("provider response exceeds the configured size limit")
        if "error" in payload:
            raise ProviderCodecError("provider returned an error envelope")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderCodecError("provider response has no choice")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise ProviderCodecError("provider choice is malformed")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise ProviderCodecError("provider message is malformed")
        finish_reason = first.get("finish_reason") or "unknown"
        if not isinstance(finish_reason, str):
            raise ProviderCodecError("provider finish reason is malformed")

        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ProviderCodecError("provider Tool Calls are malformed")
        tool_calls: list[ProviderRawToolCall] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                raise ProviderCodecError("provider Tool Call is malformed")
            function = raw_call.get("function")
            if not isinstance(function, Mapping):
                raise ProviderCodecError("provider function call is malformed")
            tool_calls.append(
                ProviderRawToolCall(
                    tool_call_id=raw_call.get("id"),
                    name=function.get("name"),
                    raw_arguments_json=function.get("arguments"),
                )
            )

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ProviderCodecError("provider content must be text")
        structured_payload: dict[str, Any] | None = None
        assistant_text: str | None = None
        if request.structured_output is not None:
            if tool_calls:
                assistant_text = (
                    content
                    if isinstance(content, str) and content.strip()
                    else None
                )
            elif content is None:
                raise ProviderCodecError("structured response has an invalid shape")
            else:
                try:
                    structured_payload = parse_json_object(
                        content,
                        max_bytes=max_response_bytes,
                    )
                except ProviderJsonObjectError as exc:
                    raise ProviderStructuredJsonEnvelopeError(
                        kind=exc.kind,
                        provider_receipt_ref=provider_receipt_ref,
                        response_hash=response_hash,
                    ) from exc
        else:
            assistant_text = (
                content
                if isinstance(content, str) and content.strip()
                else None
            )

        usage = self._decode_usage(payload.get("usage"))
        return ProviderDecodedResponse(
            call_ref=request.call_ref,
            finish_reason=finish_reason,
            assistant_text=assistant_text,
            structured_payload=structured_payload,
            tool_calls=tuple(tool_calls),
            usage=usage,
            provider_receipt_ref=provider_receipt_ref,
        )

    @staticmethod
    def _encode_message(message: ProviderMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role}
        if message.content is not None:
            payload["content"] = message.content
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments_json,
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id
        if message.name is not None:
            payload["name"] = message.name
        return payload

    @staticmethod
    def _decode_usage(raw: Any) -> ProviderUsage:
        if raw is None:
            return ProviderUsage(usage_verified=False)
        if not isinstance(raw, Mapping):
            raise ProviderCodecError("provider usage is malformed")

        def optional_int(name: str) -> int | None:
            value = raw.get(name)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProviderCodecError("provider usage value is malformed")
            return value

        input_tokens = optional_int("prompt_tokens")
        output_tokens = optional_int("completion_tokens")
        total_tokens = optional_int("total_tokens")
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            usage_verified=any(
                value is not None
                for value in (input_tokens, output_tokens, total_tokens)
            ),
        )


class ProviderTransport(Protocol):
    async def invoke(self, request: ProviderWireRequest) -> Mapping[str, Any]: ...


class DisabledProviderTransport:
    async def invoke(self, request: ProviderWireRequest) -> Mapping[str, Any]:
        del request
        raise ProviderTransportFailure(
            ProviderFailure(
                code=ProviderErrorCode.DISABLED,
                safe_message="provider transport is disabled",
            )
        )


class StaticProviderTransport:
    """In-memory response transport for later authorized contract tests only."""

    def __init__(self, responses: Mapping[str, Mapping[str, Any]]):
        self._responses = MappingProxyType(
            {key: dict(value) for key, value in responses.items()}
        )

    async def invoke(self, request: ProviderWireRequest) -> Mapping[str, Any]:
        try:
            return self._responses[request.call_ref]
        except KeyError as exc:
            raise ProviderTransportFailure(
                ProviderFailure(
                    code=ProviderErrorCode.TRANSPORT_UNAVAILABLE,
                    safe_message="no static provider response is configured",
                )
            ) from exc


class ProviderTokenCounter(Protocol):
    async def count(
        self,
        *,
        request: ProviderRenderedRequest,
        payload: Mapping[str, Any],
    ) -> int: ...


class DisabledProviderTokenCounter:
    async def count(
        self,
        *,
        request: ProviderRenderedRequest,
        payload: Mapping[str, Any],
    ) -> int:
        del request, payload
        raise ProviderTransportFailure(
            ProviderFailure(
                code=ProviderErrorCode.DISABLED,
                safe_message="provider request token counter is disabled",
            )
        )


class StaticProviderTokenCounter:
    """Precomputed final-wire counts for later authorized contract tests only."""

    def __init__(self, counts: Mapping[str, int]):
        self._counts = MappingProxyType(dict(counts))

    async def count(
        self,
        *,
        request: ProviderRenderedRequest,
        payload: Mapping[str, Any],
    ) -> int:
        del payload
        try:
            value = self._counts[request.call_ref]
        except KeyError as exc:
            raise ProviderTransportFailure(
                ProviderFailure(
                    code=ProviderErrorCode.TRANSPORT_UNAVAILABLE,
                    safe_message="no static provider token count is configured",
                )
            ) from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProviderTransportFailure(
                ProviderFailure(
                    code=ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                    safe_message="provider token counter returned an invalid count",
                )
            )
        return value


class ProviderRequestRenderer:
    """Render governed Context and Registry snapshots into provider-neutral input."""

    def __init__(self, projector: ProviderSchemaProjector | None = None) -> None:
        self._projector = projector or ProviderSchemaProjector()

    def render(
        self,
        *,
        invocation: ProviderInvocationRequest,
        capabilities: ProviderCapabilities,
    ) -> ProviderRenderedRequest:
        snapshot = invocation.context.snapshot
        if not capabilities.enabled:
            self._raise(
                ProviderErrorCode.DISABLED,
                "provider capability is disabled",
            )
        if (
            snapshot.model_profile_ref != capabilities.model_profile_ref
            or snapshot.model_profile_hash != capabilities.model_profile_hash
        ):
            self._raise(
                ProviderErrorCode.CONTEXT_REJECTED,
                "provider capability does not match the Context model profile",
            )
        if invocation.max_output_tokens > capabilities.max_output_tokens:
            self._raise(
                ProviderErrorCode.CAPABILITY_UNAVAILABLE,
                "requested output budget exceeds provider capability",
            )

        tools = self._render_tools(
            invocation=invocation,
            capabilities=capabilities,
        )
        structured_output = self._render_structured_output(
            invocation=invocation,
            capabilities=capabilities,
        )
        if (
            tools
            and structured_output is not None
            and not capabilities.supports_tool_calls_with_structured_output
        ):
            self._raise(
                ProviderErrorCode.CAPABILITY_UNAVAILABLE,
                "provider cannot combine Tool Calls with structured output",
            )
        messages = self._render_messages(
            invocation=invocation,
            capabilities=capabilities,
        )
        registry = invocation.registry_snapshot
        body = {
            "call_ref": invocation.call_ref,
            "task_ref": invocation.task_ref,
            "state_version": invocation.state_version,
            "consumer": invocation.consumer,
            "context_snapshot_ref": snapshot.snapshot_ref,
            "context_snapshot_hash": snapshot.snapshot_hash,
            "provider_ref": capabilities.provider_ref,
            "model_ref": capabilities.model_ref,
            "capability_ref": capabilities.capability_ref,
            "capability_hash": capabilities.capability_hash,
            "messages": messages,
            "tools": tools,
            "structured_output": structured_output,
            "tool_choice": invocation.tool_choice,
            "parallel_tool_calls": False,
            "max_output_tokens": invocation.max_output_tokens,
            "input_token_limit": snapshot.effective_input_budget,
            "assembled_estimated_input_tokens": snapshot.estimated_input_tokens or 0,
            "registry_snapshot_ref": None if registry is None else registry.snapshot_ref,
            "registry_snapshot_hash": None if registry is None else registry.snapshot_hash,
            "visible_tools_hash": None if registry is None else registry.visible_tools_hash,
        }
        return ProviderRenderedRequest(
            **body,
            request_hash=canonical_hash(_json_ready(body)),
        )

    def _render_tools(
        self,
        *,
        invocation: ProviderInvocationRequest,
        capabilities: ProviderCapabilities,
    ) -> tuple[ProviderFunctionDefinition, ...]:
        if invocation.tool_choice is ProviderToolChoice.NONE:
            return ()
        registry = invocation.registry_snapshot
        if registry is None:
            return ()
        visible_names = (
            registry.visible_tool_names
            if invocation.tool_name_filter is None
            else invocation.tool_name_filter
        )
        indexed = {
            entry.name: entry.model_contract for entry in registry.entries
        }
        contracts = tuple(indexed[name] for name in visible_names)
        if len(contracts) > capabilities.max_visible_tools:
            self._raise(
                ProviderErrorCode.CAPABILITY_UNAVAILABLE,
                "visible Tool count exceeds provider capability",
            )
        if contracts and not capabilities.supports_function_calling:
            self._raise(
                ProviderErrorCode.CAPABILITY_UNAVAILABLE,
                "provider does not support Function Calling",
            )
        rendered: list[ProviderFunctionDefinition] = []
        for contract in contracts:
            try:
                projection = self._projector.project(
                    contract.input_schema,
                    capabilities=capabilities,
                    strict_mode=invocation.tool_strict_mode,
                    strict_supported=capabilities.supports_strict_tools,
                )
            except ProviderCodecError as exc:
                raise ProviderAdapterError(
                    ProviderFailure(
                        code=ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                        safe_message="Tool input schema cannot be projected safely",
                    )
                ) from exc
            if not projection.provider_compatible:
                self._raise(
                    ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                    "Tool input schema is incompatible with provider capabilities",
                )
            if (
                invocation.tool_strict_mode is ProviderStrictMode.REQUIRED
                and not projection.strict_enabled
            ):
                self._raise(
                    ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                    "required strict Tool schema is unavailable",
                )
            rendered.append(
                ProviderFunctionDefinition(
                    name=contract.name,
                    description=contract.description,
                    parameters=projection.projected_schema,
                    parameters_hash=projection.schema_hash,
                    strict_enabled=projection.strict_enabled,
                    projection_issues=projection.issues,
                )
            )
        return tuple(rendered)

    def _render_structured_output(
        self,
        *,
        invocation: ProviderInvocationRequest,
        capabilities: ProviderCapabilities,
    ) -> ProviderStructuredOutputProjection | None:
        spec = invocation.structured_output
        if spec is None:
            return None
        if not capabilities.supports_structured_output:
            self._raise(
                ProviderErrorCode.CAPABILITY_UNAVAILABLE,
                "provider does not support structured output",
            )
        try:
            projection = self._projector.project(
                spec.output_schema,
                capabilities=capabilities,
                strict_mode=spec.strict_mode,
                strict_supported=capabilities.supports_strict_structured_output,
            )
        except ProviderCodecError as exc:
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                    safe_message="structured output schema cannot be projected safely",
                )
            ) from exc
        if not projection.provider_compatible:
            self._raise(
                ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                "structured output schema is incompatible with provider capabilities",
            )
        if spec.strict_mode is ProviderStrictMode.REQUIRED and not projection.strict_enabled:
            self._raise(
                ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                "required strict structured output is unavailable",
            )
        return ProviderStructuredOutputProjection(
            schema_name=spec.schema_name,
            output_schema=projection.projected_schema,
            schema_hash=projection.schema_hash,
            strict_enabled=projection.strict_enabled,
            projection_issues=projection.issues,
        )

    def _render_messages(
        self,
        *,
        invocation: ProviderInvocationRequest,
        capabilities: ProviderCapabilities,
    ) -> tuple[ProviderMessage, ...]:
        policy_messages: list[ProviderMessage] = []
        remaining_messages: list[ProviderMessage] = []
        visible_names = (
            set()
            if invocation.registry_snapshot is None
            else set(invocation.registry_snapshot.visible_tool_names)
        )
        for entry in invocation.context.projection_entries:
            if entry.kind is ContextEntryKind.TOOL_CONTRACT:
                continue
            if entry.kind is ContextEntryKind.ACTIVE_TOOL_CALL:
                if (
                    entry.tool_name is None
                    or entry.protocol_pair_ref is None
                    or entry.tool_name not in visible_names
                ):
                    self._raise(
                        ProviderErrorCode.CONTEXT_REJECTED,
                        "active Tool Call protocol entry is invalid",
                    )
                parse_json_object(
                    entry.content,
                    max_bytes=capabilities.max_arguments_bytes,
                )
                remaining_messages.append(
                    ProviderMessage(
                        role="assistant",
                        tool_calls=(
                            ProviderMessageToolCall(
                                tool_call_id=entry.protocol_pair_ref,
                                name=entry.tool_name,
                                arguments_json=entry.content,
                                raw_arguments_hash=canonical_hash(entry.content),
                            ),
                        ),
                        source_entry_refs=(entry.entry_ref,),
                    )
                )
                continue
            if entry.kind is ContextEntryKind.ACTIVE_TOOL_RESULT:
                if (
                    entry.tool_name is None
                    or entry.protocol_pair_ref is None
                    or entry.tool_name not in visible_names
                ):
                    self._raise(
                        ProviderErrorCode.CONTEXT_REJECTED,
                        "active Tool result protocol entry is invalid",
                    )
                remaining_messages.append(
                    ProviderMessage(
                        role="tool",
                        content=entry.content,
                        tool_call_id=entry.protocol_pair_ref,
                        name=entry.tool_name,
                        source_entry_refs=(entry.entry_ref,),
                    )
                )
                continue
            if entry.kind is ContextEntryKind.CURRENT_USER_MESSAGE:
                remaining_messages.append(
                    ProviderMessage(
                        role="user",
                        content=entry.content,
                        source_entry_refs=(entry.entry_ref,),
                    )
                )
                continue
            if entry.trust_class in {
                ContextTrustClass.TRUSTED_POLICY,
                ContextTrustClass.TRUSTED_RUNTIME,
            }:
                policy_messages.append(
                    ProviderMessage(
                        role="system",
                        content=entry.content,
                        source_entry_refs=(entry.entry_ref,),
                    )
                )
                continue
            wrapper = canonical_json(
                {
                    "context_data": {
                        "entry_ref": entry.entry_ref,
                        "kind": entry.kind.value,
                        "authority_label": entry.authority_label,
                        "untrusted_data": True,
                        "content": entry.content,
                    }
                }
            )
            remaining_messages.append(
                ProviderMessage(
                    role="user",
                    content=wrapper,
                    source_entry_refs=(entry.entry_ref,),
                )
            )
        if invocation.runtime_input is not None:
            runtime_input = invocation.runtime_input
            policy_messages.append(
                ProviderMessage(
                    role="system",
                    content=canonical_json(
                        {
                            "runtime_input": {
                                "input_ref": runtime_input.input_ref,
                                "input_kind": runtime_input.input_kind,
                                "payload": runtime_input.payload,
                                "payload_hash": runtime_input.payload_hash,
                            }
                        }
                    ),
                )
            )
        messages = (*policy_messages, *remaining_messages)
        if not messages:
            self._raise(
                ProviderErrorCode.CONTEXT_REJECTED,
                "Context projection produced no provider messages",
            )
        return messages

    @staticmethod
    def _raise(code: ProviderErrorCode, safe_message: str) -> None:
        raise ProviderAdapterError(
            ProviderFailure(code=code, safe_message=safe_message)
        )


class ProviderAdapter:
    """One provider call boundary; default construction is fully disabled."""

    def __init__(
        self,
        *,
        capabilities: ProviderCapabilities | None = None,
        renderer: ProviderRequestRenderer | None = None,
        codec: ProviderRequestCodec | None = None,
        token_counter: ProviderTokenCounter | None = None,
        transport: ProviderTransport | None = None,
    ) -> None:
        self.capabilities = capabilities or ProviderCapabilities.disabled()
        self._renderer = renderer or ProviderRequestRenderer()
        self._codec = codec or OpenAICompatibleChatCodec()
        self._token_counter = token_counter or DisabledProviderTokenCounter()
        self._transport = transport or DisabledProviderTransport()
        if self._codec.codec_ref != self.capabilities.codec_ref:
            raise ValueError("Provider codec does not match frozen capabilities")

    async def invoke(self, invocation: ProviderInvocationRequest) -> ProviderModelResult:
        try:
            rendered = self._renderer.render(
                invocation=invocation,
                capabilities=self.capabilities,
            )
            payload = self._codec.encode(rendered)
            serialized_input_tokens = await self._token_counter.count(
                request=rendered,
                payload=payload,
            )
            if serialized_input_tokens > rendered.input_token_limit:
                raise ProviderAdapterError(
                    ProviderFailure(
                        code=ProviderErrorCode.INPUT_LIMIT_EXCEEDED,
                        safe_message="serialized provider input exceeds the Context budget",
                    )
                )
            wire = ProviderWireRequest(
                call_ref=rendered.call_ref,
                payload=payload,
                payload_hash=canonical_hash(payload),
                serialized_input_tokens=serialized_input_tokens,
                input_token_limit=rendered.input_token_limit,
            )
            raw_payload = await self._transport.invoke(wire)
            decoded = self._codec.decode(
                request=rendered,
                payload=raw_payload,
                max_response_bytes=self.capabilities.max_response_bytes,
            )
            return self._normalize(
                invocation=invocation,
                rendered=rendered,
                wire=wire,
                decoded=decoded,
            )
        except ProviderAdapterError:
            raise
        except ProviderTransportFailure as exc:
            raise ProviderAdapterError(exc.failure) from exc
        except ProviderToolCallLimitExceeded as exc:
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.TOOL_CALL_LIMIT_EXCEEDED,
                    safe_message=str(exc),
                )
            ) from exc
        except ProviderToolNameNotVisible as exc:
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.TOOL_NAME_NOT_VISIBLE,
                    safe_message=str(exc),
                )
            ) from exc
        except ProviderStructuredJsonEnvelopeError as exc:
            size_limited = (
                exc.kind is ProviderJsonObjectFailureKind.SIZE_LIMIT
            )
            raise ProviderAdapterError(
                ProviderFailure(
                    code=(
                        ProviderErrorCode.RESPONSE_JSON_SIZE_LIMIT
                        if size_limited
                        else ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID
                    ),
                    safe_message=(
                        "provider structured response exceeded the JSON size limit"
                        if size_limited
                        else (
                            "provider structured response was not one valid "
                            "JSON object"
                        )
                    ),
                    retryable=not size_limited,
                    provider_receipt_ref=exc.provider_receipt_ref,
                    response_hash=exc.response_hash,
                )
            ) from exc
        except ProviderCodecError as exc:
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                    safe_message=f"provider codec rejected response: {str(exc)[:200]}",
                )
            ) from exc
        except (ValidationError, TypeError, ValueError) as exc:
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                    safe_message="provider request or response violated the canonical contract",
                )
            ) from exc
        except Exception as exc:
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                    safe_message="provider invocation is unavailable",
                )
            ) from exc

    def _normalize(
        self,
        *,
        invocation: ProviderInvocationRequest,
        rendered: ProviderRenderedRequest,
        wire: ProviderWireRequest,
        decoded: ProviderDecodedResponse,
    ) -> ProviderModelResult:
        if decoded.call_ref != invocation.call_ref:
            raise ProviderCodecError("provider response belongs to another call")
        proposals: list[ProviderToolCallProposal] = []
        registry = invocation.registry_snapshot
        if decoded.tool_calls:
            if registry is None or invocation.tool_choice is ProviderToolChoice.NONE:
                raise ProviderCodecError("unexpected provider Tool Calls")
            if len(decoded.tool_calls) > self.capabilities.max_tool_calls_per_response:
                raise ProviderToolCallLimitExceeded(
                    actual_count=len(decoded.tool_calls),
                    limit=self.capabilities.max_tool_calls_per_response,
                )
            visible = set(
                registry.visible_tool_names
                if invocation.tool_name_filter is None
                else invocation.tool_name_filter
            )
            for sequence, raw_call in enumerate(decoded.tool_calls, start=1):
                if raw_call.name not in visible:
                    raise ProviderToolNameNotVisible()
                arguments = parse_json_object(
                    raw_call.raw_arguments_json,
                    max_bytes=self.capabilities.max_arguments_bytes,
                )
                proposals.append(
                    ProviderToolCallProposal(
                        model_turn_ref=invocation.call_ref,
                        provider_tool_call_id=raw_call.tool_call_id,
                        sequence=sequence,
                        task_ref=invocation.task_ref,
                        context_snapshot_ref=invocation.context.snapshot.snapshot_ref,
                        state_version=invocation.state_version,
                        tool_name=raw_call.name,
                        raw_arguments_json=raw_call.raw_arguments_json,
                        raw_arguments_hash=canonical_hash(raw_call.raw_arguments_json),
                        arguments=arguments,
                        arguments_hash=canonical_hash(arguments),
                        registry_snapshot_ref=registry.snapshot_ref,
                        registry_snapshot_hash=registry.snapshot_hash,
                        visible_tools_hash=registry.visible_tools_hash,
                        authorization_snapshot_ref=(
                            invocation.context.snapshot.authorization_snapshot_ref
                        ),
                    )
                )
        if proposals:
            output_kind = ProviderOutputKind.TOOL_CALLS
        elif decoded.structured_payload is not None:
            if invocation.structured_output is None:
                raise ProviderCodecError("unexpected structured provider output")
            output_kind = ProviderOutputKind.STRUCTURED
        else:
            if invocation.structured_output is not None:
                raise ProviderCodecError("provider omitted required structured output")
            output_kind = ProviderOutputKind.TEXT
        response_body = {
            "call_ref": invocation.call_ref,
            "finish_reason": decoded.finish_reason,
            "assistant_text": decoded.assistant_text,
            "structured_payload": decoded.structured_payload,
            "tool_call_proposals": [
                proposal.model_dump(mode="json") for proposal in proposals
            ],
            "usage": decoded.usage.model_dump(mode="json"),
            "provider_receipt_ref": decoded.provider_receipt_ref,
        }
        response_hash = canonical_hash(response_body)
        return ProviderModelResult(
            result_ref=(
                "model-result:" + response_hash.removeprefix("sha256:")
            ),
            call_ref=invocation.call_ref,
            task_ref=invocation.task_ref,
            state_version=invocation.state_version,
            consumer=invocation.consumer,
            context_snapshot_ref=invocation.context.snapshot.snapshot_ref,
            output_kind=output_kind,
            assistant_text=decoded.assistant_text,
            structured_payload=decoded.structured_payload,
            tool_call_proposals=tuple(proposals),
            finish_reason=decoded.finish_reason,
            usage=decoded.usage,
            serialized_input_tokens=wire.serialized_input_tokens,
            provider_receipt_ref=decoded.provider_receipt_ref,
            response_hash=response_hash,
        )


def bridge_tool_call_proposals(
    result: ProviderModelResult,
    *,
    active_task: AgentTaskState,
    action_ref: str,
) -> tuple[ToolCallRequest, ...]:
    """Bridge normalized proposals to Gateway requests without executing them."""

    if result.output_kind is not ProviderOutputKind.TOOL_CALLS:
        return ()
    return bind_normalized_tool_call_proposals(
        result.tool_call_proposals,
        proposal_task_ref=result.task_ref,
        proposal_state_version=result.state_version,
        active_task=active_task,
        action_ref=action_ref,
    )


def bind_normalized_tool_call_proposals(
    proposals: tuple[ProviderToolCallProposal, ...],
    *,
    proposal_task_ref: str,
    proposal_state_version: int,
    active_task: AgentTaskState,
    action_ref: str,
) -> tuple[ToolCallRequest, ...]:
    """Bind frozen proposals to one later accepted Tool action, without execution."""

    if (
        not proposals
        or active_task.status is not AgentTaskStatus.RUNNING
        or active_task.task_id != proposal_task_ref
        or active_task.in_flight_action_ref != action_ref
        or active_task.state_version <= proposal_state_version
    ):
        raise ProviderAdapterError(
            ProviderFailure(
                code=ProviderErrorCode.CONTEXT_REJECTED,
                safe_message="Tool Call proposals cannot bind to the active action",
            )
        )
    requests: list[ToolCallRequest] = []
    for proposal in proposals:
        if (
            proposal.task_ref != proposal_task_ref
            or proposal.state_version != proposal_state_version
        ):
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.CONTEXT_REJECTED,
                    safe_message="Tool Call proposal scope is inconsistent",
                )
            )
        call_identity = canonical_hash(
            {
                "model_turn_ref": proposal.model_turn_ref,
                "provider_tool_call_id": proposal.provider_tool_call_id,
                "arguments_hash": proposal.arguments_hash,
            }
        ).removeprefix("sha256:")
        requests.append(
            ToolCallRequest(
                call_ref=f"tool-call:{call_identity}",
                provider_tool_call_id=proposal.provider_tool_call_id,
                model_turn_ref=proposal.model_turn_ref,
                sequence=proposal.sequence,
                task_ref=proposal.task_ref,
                action_ref=action_ref,
                context_snapshot_ref=proposal.context_snapshot_ref,
                state_version=active_task.state_version,
                tool_name=proposal.tool_name,
                arguments=proposal.arguments,
                registry_snapshot_ref=proposal.registry_snapshot_ref,
                registry_snapshot_hash=proposal.registry_snapshot_hash,
                visible_tools_hash=proposal.visible_tools_hash,
                authorization_snapshot_ref=proposal.authorization_snapshot_ref,
            )
        )
    return tuple(requests)


ModelT = TypeVar("ModelT", bound=BaseModel)


class StructuredModelCallBridge:
    """Invoke one structured capability and revalidate with its Pydantic model."""

    def __init__(self, adapter: ProviderAdapter | None = None) -> None:
        self._adapter = adapter or ProviderAdapter()

    async def invoke(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        output_model: type[ModelT],
        schema_name: str,
        runtime_input: ProviderRuntimeInput | None = None,
        registry_snapshot: RegistrySnapshot | None = None,
        tool_choice: ProviderToolChoice = ProviderToolChoice.NONE,
        strict_mode: ProviderStrictMode = ProviderStrictMode.PREFERRED,
        max_output_tokens: int | None = None,
    ) -> ModelT:
        if task.status is not AgentTaskStatus.RUNNING:
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.CONTEXT_REJECTED,
                    safe_message="structured model call requires a running task",
                )
            )
        spec = ProviderStructuredOutputSpec.from_model(
            schema_name=schema_name,
            output_model=output_model,
            strict_mode=strict_mode,
        )
        call_body = {
            "task_ref": task.task_id,
            "state_version": task.state_version,
            "consumer": context.snapshot.consumer.value,
            "context_snapshot_ref": context.snapshot.snapshot_ref,
            "context_snapshot_hash": context.snapshot.snapshot_hash,
            "runtime_input_hash": None if runtime_input is None else runtime_input.payload_hash,
            "output_schema_hash": spec.output_schema_hash,
            "registry_snapshot_hash": None if registry_snapshot is None else registry_snapshot.snapshot_hash,
        }
        invocation = ProviderInvocationRequest(
            call_ref=(
                "model-call:" + canonical_hash(call_body).removeprefix("sha256:")
            ),
            task_ref=task.task_id,
            state_version=task.state_version,
            consumer=context.snapshot.consumer,
            context=context,
            registry_snapshot=registry_snapshot,
            runtime_input=runtime_input,
            structured_output=spec,
            tool_choice=tool_choice,
            tool_strict_mode=strict_mode,
            max_output_tokens=(
                max_output_tokens
                if max_output_tokens is not None
                else min(
                    self._adapter.capabilities.max_output_tokens,
                    context.snapshot.reserved_output_tokens,
                )
            ),
        )
        result = await self._adapter.invoke(invocation)
        if (
            result.output_kind is not ProviderOutputKind.STRUCTURED
            or result.structured_payload is None
        ):
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                    safe_message="provider did not return the required structured result",
                    provider_receipt_ref=result.provider_receipt_ref,
                )
            )
        try:
            return output_model.model_validate_json(
                canonical_json(result.structured_payload)
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ProviderAdapterError(
                ProviderFailure(
                    code=ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                    safe_message="structured provider output failed Runtime validation",
                    provider_receipt_ref=result.provider_receipt_ref,
                )
            ) from exc
