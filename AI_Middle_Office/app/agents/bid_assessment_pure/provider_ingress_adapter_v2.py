"""Deterministic, default-disabled implementation of Provider JSON ingress V2."""

from __future__ import annotations

import json
import re
from typing import Any, Never

from .provider_ingress_v2 import (
    ProviderBoundaryFailure,
    ProviderBoundaryFailureCode,
    ProviderBoundaryFailureStage,
    ProviderBoundaryRejected,
    ProviderBoundaryV2Config,
    ProviderIngressNormalizationStep,
    ProviderIngressPayloadKind,
    ProviderIngressReceipt,
    ProviderIngressRequest,
    ProviderIngressResult,
    raw_text_hash,
)
from .tool_runtime import canonical_hash


_JSON_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)


class _DuplicateJsonKey(ValueError):
    pass


class _NonFiniteJsonNumber(ValueError):
    pass


class DeterministicProviderJsonIngressAdapter:
    """Normalize one JSON Object without guessing or changing field values."""

    def __init__(self, config: ProviderBoundaryV2Config | None = None) -> None:
        self._config = config or ProviderBoundaryV2Config()

    @property
    def config(self) -> ProviderBoundaryV2Config:
        return self._config

    def normalize(
        self,
        *,
        request: ProviderIngressRequest,
        raw_value: str,
    ) -> ProviderIngressResult:
        self._require_enabled()
        self._validate_raw_binding(request=request, raw_value=raw_value)
        effective_limit = min(
            request.max_size_bytes,
            self._configured_limit(request.payload_kind),
        )
        if request.raw_size_bytes > effective_limit:
            self._reject(
                ProviderBoundaryFailureCode.JSON_SIZE_LIMIT,
                "provider JSON exceeded the configured ingress limit",
            )

        candidate = raw_value.strip()
        steps: list[ProviderIngressNormalizationStep] = []
        fenced = _JSON_FENCE.fullmatch(candidate)
        if fenced is not None and self._config.allow_markdown_fence_removal:
            candidate = fenced.group("body").strip()
            steps.append(
                ProviderIngressNormalizationStep.MARKDOWN_FENCE_REMOVED
            )

        payload = self._parse_direct(candidate)
        if payload is None:
            payload = self._extract_single_object(candidate)
            steps.append(
                ProviderIngressNormalizationStep.SINGLE_JSON_OBJECT_EXTRACTED
            )

        receipt = ProviderIngressReceipt.build(
            request=request,
            normalized_payload=payload,
            normalization_steps=tuple(steps),
            schema_validated=False,
        )
        return ProviderIngressResult(
            request_ref=request.request_ref,
            payload=payload,
            payload_hash=canonical_hash(payload),
            receipt=receipt,
        )

    def _require_enabled(self) -> None:
        if not self._config.enabled:
            self._reject(
                ProviderBoundaryFailureCode.BOUNDARY_DISABLED,
                "provider boundary V2 is disabled",
            )

    def _validate_raw_binding(
        self,
        *,
        request: ProviderIngressRequest,
        raw_value: str,
    ) -> None:
        try:
            encoded_size = len(raw_value.encode("utf-8"))
            digest = raw_text_hash(raw_value)
        except UnicodeEncodeError:
            self._reject(
                ProviderBoundaryFailureCode.JSON_ENCODING_INVALID,
                "provider JSON was not valid UTF-8 text",
            )
        if encoded_size != request.raw_size_bytes or digest != request.raw_payload_hash:
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "provider JSON did not match its ingress request",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )

    def _configured_limit(self, kind: ProviderIngressPayloadKind) -> int:
        if kind is ProviderIngressPayloadKind.TOOL_ARGUMENTS:
            return self._config.max_tool_arguments_bytes
        return self._config.max_response_bytes

    def _parse_direct(self, candidate: str) -> dict[str, Any] | None:
        try:
            value = self._loads(candidate)
        except _DuplicateJsonKey:
            self._reject(
                ProviderBoundaryFailureCode.JSON_DUPLICATE_KEY,
                "provider JSON contained a duplicate object key",
            )
        except _NonFiniteJsonNumber:
            self._reject(
                ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
                "provider JSON contained a non-finite number",
            )
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            self._reject(
                ProviderBoundaryFailureCode.JSON_NON_OBJECT,
                "provider JSON root must be an object",
            )
        return value

    def _extract_single_object(self, candidate: str) -> dict[str, Any]:
        if not self._config.allow_single_object_extraction:
            self._reject(
                ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
                "provider output was not one JSON object",
                structurally_repairable=True,
            )
        spans, truncated = self._top_level_object_spans(candidate)
        if truncated:
            self._reject(
                ProviderBoundaryFailureCode.JSON_TRUNCATED,
                "provider JSON object was truncated",
            )
        if len(spans) > 1:
            self._reject(
                ProviderBoundaryFailureCode.JSON_MULTIPLE_OBJECTS,
                "provider output contained multiple JSON objects",
            )
        if not spans:
            self._reject(
                ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
                "provider output did not contain one JSON object",
                structurally_repairable=True,
            )
        start, end = spans[0]
        try:
            value = self._loads(candidate[start:end])
        except _DuplicateJsonKey:
            self._reject(
                ProviderBoundaryFailureCode.JSON_DUPLICATE_KEY,
                "provider JSON contained a duplicate object key",
            )
        except _NonFiniteJsonNumber:
            self._reject(
                ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
                "provider JSON contained a non-finite number",
            )
        except json.JSONDecodeError:
            self._reject(
                ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
                "provider output contained an invalid JSON object",
            )
        if not isinstance(value, dict):
            self._reject(
                ProviderBoundaryFailureCode.JSON_NON_OBJECT,
                "provider JSON root must be an object",
            )
        return value

    @staticmethod
    def _loads(raw: str) -> Any:
        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise _DuplicateJsonKey(key)
                result[key] = value
            return result

        def reject_constant(value: str) -> Never:
            raise _NonFiniteJsonNumber(value)

        return json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )

    @staticmethod
    def _top_level_object_spans(raw: str) -> tuple[list[tuple[int, int]], bool]:
        spans: list[tuple[int, int]] = []
        start: int | None = None
        depth = 0
        in_string = False
        escaped = False
        for index, character in enumerate(raw):
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif character == "}" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append((start, index + 1))
                    start = None
        return spans, depth > 0 or in_string

    @staticmethod
    def _reject(
        code: ProviderBoundaryFailureCode,
        safe_message: str,
        *,
        stage: ProviderBoundaryFailureStage = ProviderBoundaryFailureStage.INGRESS,
        structurally_repairable: bool = False,
    ) -> Never:
        raise ProviderBoundaryRejected(
            ProviderBoundaryFailure(
                stage=stage,
                code=code,
                safe_message=safe_message,
                structurally_repairable=structurally_repairable,
            )
        )
