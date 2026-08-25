"""Official DeepSeek adapter for the explicitly enabled local Pure Agent.

Construction is side-effect free.  The API key and endpoint are accepted only
through an explicit configuration object, and network I/O happens only inside
``ProviderAdapter.invoke``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationError

from .action_runtime import (
    ActionLoopContractRejected,
    MainAgentDecisionRequest,
    MainAgentModelDecision,
    MainAgentProviderOutcome,
    ProviderMainAgentActionProvider,
    ToolCallBatchAction,
)
from .common import StrictContract, ToolName
from .provider_runtime import (
    OpenAICompatibleChatCodec,
    ProviderAdapter,
    ProviderAdapterError,
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderFailure,
    ProviderInvocationRequest,
    ProviderModelResult,
    ProviderOutputKind,
    ProviderRenderedRequest,
    ProviderRuntimeInput,
    ProviderStructuredOutputSpec,
    ProviderStrictMode,
    ProviderTokenCounter,
    ProviderToolCallProposal,
    ProviderToolChoice,
    ProviderTransportFailure,
    ProviderWireRequest,
    parse_json_object,
)
from .provider_answer_projection import (
    ProviderDecisionProjection,
    project_provider_decision,
    provider_action_payload_schemas,
    provider_answer_business_rules,
)
from .runtime import (
    ContextAssemblyResult,
    ContextConsumer,
    ContextEntryKind,
    ModelContextProfile,
)
from .tool_runtime import RegistrySnapshot, canonical_hash, canonical_json


_OFFICIAL_HOST = "api.deepseek.com"
_OFFICIAL_PATHS = frozenset({"/chat/completions", "/v1/chat/completions"})
_INVALID_TOOL_ARGUMENTS_SAFE_MESSAGE = (
    "provider codec rejected response: value is not valid JSON"
)


class OfficialDeepSeekConfigurationError(ValueError):
    """The local Provider configuration is missing or outside its allowlist."""


class ProviderToolDecisionCallProjection(StrictContract):
    """Compact untrusted Tool selection repaired without Function Calling."""

    tool_name: ToolName
    arguments: dict[str, Any]


class ProviderToolDecisionProjection(StrictContract):
    """Provider-visible recovery shape; Runtime injects every authority field."""

    concise_basis: str = Field(min_length=1, max_length=500)
    calls: tuple[ProviderToolDecisionCallProjection, ...] = Field(
        min_length=1,
        max_length=4,
    )


@dataclass(frozen=True, slots=True)
class OfficialDeepSeekConfig:
    api_key: str = field(repr=False)
    chat_url: str = "https://api.deepseek.com/chat/completions"
    model_ref: str = "deepseek-v4-flash"
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        endpoint = urlsplit(self.chat_url)
        if (
            not self.api_key.strip()
            or endpoint.scheme != "https"
            or endpoint.hostname != _OFFICIAL_HOST
            or endpoint.username
            or endpoint.password
            or endpoint.path not in _OFFICIAL_PATHS
            or endpoint.query
            or endpoint.fragment
            or not self.model_ref.strip()
            or not 30 <= int(self.timeout_seconds) <= 300
        ):
            raise OfficialDeepSeekConfigurationError(
                "official DeepSeek local configuration is invalid"
            )


class OfficialDeepSeekChatCodec(OpenAICompatibleChatCodec):
    """Use JSON Object mode while retaining Pydantic as final authority."""

    codec_ref = "provider-codec:deepseek-official-chat-v1"

    def encode(self, request: ProviderRenderedRequest) -> dict[str, Any]:
        payload = super().encode(request)
        projection = request.structured_output
        if projection is not None:
            payload["messages"].insert(
                0,
                {
                    "role": "system",
                    "content": canonical_json(
                        {
                            "structured_output_instruction": (
                                "只返回一个有效 JSON 对象，不要输出 Markdown、代码围栏或解释；"
                                "对象必须满足以下 JSON Schema。Runtime 将使用 Pydantic 再校验。"
                            ),
                            "schema_name": projection.schema_name,
                            "json_schema": projection.output_schema,
                        }
                    ),
                },
            )
            payload["response_format"] = {"type": "json_object"}
        payload["thinking"] = {"type": "disabled"}
        payload["temperature"] = 0
        payload["stream"] = False
        return payload

    def decode(
        self,
        *,
        request: ProviderRenderedRequest,
        payload: Mapping[str, Any],
        max_response_bytes: int,
    ):
        normalized: Mapping[str, Any] = payload
        if request.structured_output is not None:
            normalized = self._without_json_fence(payload)
        return super().decode(
            request=request,
            payload=normalized,
            max_response_bytes=max_response_bytes,
        )

    @staticmethod
    def _without_json_fence(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return payload
        first = choices[0]
        if not isinstance(first, Mapping):
            return payload
        message = first.get("message")
        if not isinstance(message, Mapping):
            return payload
        content = message.get("content")
        if not isinstance(content, str):
            return payload
        stripped = content.strip()
        if not (stripped.startswith("```") and stripped.endswith("```")):
            return payload
        inner = stripped[3:-3].strip()
        if inner[:4].lower() == "json":
            inner = inner[4:].strip()
        if not inner.startswith("{") or not inner.endswith("}"):
            return payload
        normalized_message = dict(message)
        normalized_message["content"] = inner
        normalized_first = dict(first)
        normalized_first["message"] = normalized_message
        normalized_choices = list(choices)
        normalized_choices[0] = normalized_first
        normalized_payload = dict(payload)
        normalized_payload["choices"] = normalized_choices
        return normalized_payload


class ConservativeWireTokenCounter(ProviderTokenCounter):
    """Bound the final wire payload, including injected schema instructions."""

    counter_ref = "provider-token-counter:deepseek-conservative-v1"

    async def count(
        self,
        *,
        request: ProviderRenderedRequest,
        payload: Mapping[str, Any],
    ) -> int:
        wire_estimate = (len(canonical_json(dict(payload)).encode("utf-8")) + 1) // 2
        return max(
            int(request.assembled_estimated_input_tokens),
            wire_estimate + 32,
        )


class OfficialDeepSeekTransport:
    """Narrow HTTPS transport that never exposes response bodies or secrets."""

    def __init__(self, config: OfficialDeepSeekConfig) -> None:
        self._config = config

    async def invoke(self, request: ProviderWireRequest) -> Mapping[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=float(self._config.timeout_seconds),
                trust_env=False,
            ) as client:
                response = await client.post(
                    self._config.chat_url,
                    headers={
                        "Authorization": f"Bearer {self._config.api_key}",
                        "Content-Type": "application/json",
                        "X-Idempotency-Key": request.call_ref,
                    },
                    json=request.payload,
                )
        except httpx.TimeoutException as exc:
            raise self._failure(
                ProviderErrorCode.TIMEOUT,
                "official DeepSeek request timed out",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._failure(
                ProviderErrorCode.TRANSPORT_UNAVAILABLE,
                "official DeepSeek transport is unavailable",
                retryable=True,
            ) from exc

        if response.status_code in {401, 403}:
            raise self._failure(
                ProviderErrorCode.AUTHENTICATION_FAILED,
                "official DeepSeek authentication was rejected",
            )
        if response.status_code == 429:
            raise self._failure(
                ProviderErrorCode.RATE_LIMITED,
                "official DeepSeek rate limit was reached",
                retryable=True,
            )
        if response.status_code >= 400:
            raise self._failure(
                ProviderErrorCode.PROVIDER_REJECTED,
                "official DeepSeek rejected the request",
                retryable=response.status_code >= 500,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise self._failure(
                ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                "official DeepSeek returned an invalid response",
            ) from exc
        if not isinstance(payload, dict):
            raise self._failure(
                ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                "official DeepSeek returned an invalid response",
            )
        return payload

    @staticmethod
    def _failure(
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> ProviderTransportFailure:
        return ProviderTransportFailure(
            ProviderFailure(
                code=code,
                safe_message=message,
                retryable=retryable,
            )
        )


def build_official_deepseek_adapter(
    *,
    config: OfficialDeepSeekConfig,
    model_profile: ModelContextProfile,
) -> ProviderAdapter:
    if model_profile.model_ref != config.model_ref:
        raise OfficialDeepSeekConfigurationError(
            "DeepSeek model and Context profile do not match"
        )
    codec = OfficialDeepSeekChatCodec()
    counter = ConservativeWireTokenCounter()
    capabilities = ProviderCapabilities.build(
        capability_ref="provider-capability:deepseek-official-local-v1",
        enabled=True,
        provider_ref=model_profile.provider_ref,
        model_ref=config.model_ref,
        model_profile_ref=model_profile.profile_ref,
        model_profile_hash=model_profile.profile_hash,
        codec_ref=codec.codec_ref,
        token_counter_ref=counter.counter_ref,
        supports_function_calling=True,
        supports_strict_tools=False,
        supports_structured_output=True,
        supports_strict_structured_output=False,
        supports_parallel_tool_calls=False,
        supports_tool_calls_with_structured_output=False,
        max_visible_tools=4,
        max_tool_calls_per_response=4,
        max_arguments_bytes=16 * 1024,
        max_response_bytes=2 * 1024 * 1024,
        max_output_tokens=model_profile.max_output_tokens,
    )
    return ProviderAdapter(
        capabilities=capabilities,
        codec=codec,
        token_counter=counter,
        transport=OfficialDeepSeekTransport(config),
    )


class DeepSeekMainAgentActionProvider:
    """Function Calling first; parse the no-Tool branch as validated JSON text."""

    def __init__(self, adapter: ProviderAdapter) -> None:
        self._adapter = adapter

    async def decide(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentProviderOutcome:
        runtime_input = ProviderRuntimeInput.from_payload(
            input_ref=request.request_ref,
            input_kind="main_agent_decision_request",
            payload={
                "request": request.model_dump(mode="json"),
                "no_tool_output_instruction": (
                    "如果无需调用工具，只返回一个满足 decision_projection_schema 的 JSON"
                    " 对象。顶层必须且只能包含 action_kind、concise_basis、payload 三个唯一"
                    " key；payload 必须满足对应 action_payload_schemas。不要输出 Context、"
                    "state_version、引用投影或其他 Runtime 权威字段；Runtime 会从当前请求注入"
                    "并用完整 Pydantic 合同再次校验。"
                ),
                "decision_projection_schema": (
                    ProviderDecisionProjection.model_json_schema()
                ),
                "action_payload_schemas": provider_action_payload_schemas(),
                "answer_business_rules": provider_answer_business_rules(),
                "valid_answer_example_when_evidence_is_sufficient": (
                    self._answer_example(context=context)
                ),
            },
        )
        call_ref = "model-call:" + canonical_hash(
            {
                "request_ref": request.request_ref,
                "request_hash": request.request_hash,
            }
        ).removeprefix("sha256:")
        invocation = ProviderInvocationRequest(
            call_ref=call_ref,
            task_ref=request.task_ref,
            state_version=request.origin_state_version,
            consumer=ContextConsumer.MAIN_AGENT,
            context=context,
            registry_snapshot=registry_snapshot,
            runtime_input=runtime_input,
            structured_output=None,
            tool_choice=(
                ProviderToolChoice.AUTO
                if request.visible_tool_names
                else ProviderToolChoice.NONE
            ),
            tool_strict_mode=ProviderStrictMode.PREFERRED,
            max_output_tokens=min(
                self._adapter.capabilities.max_output_tokens,
                context.snapshot.reserved_output_tokens,
            ),
        )
        repaired_tool_batch: ToolCallBatchAction | None = None
        repaired_tool_basis: str | None = None
        try:
            result = await self._adapter.invoke(invocation)
        except ProviderAdapterError as exc:
            if not self._is_repairable_tool_arguments_failure(
                error=exc,
                request=request,
                registry_snapshot=registry_snapshot,
            ):
                raise
            result, repaired_tool_batch, repaired_tool_basis = (
                await self._repair_invalid_tool_arguments(
                    request=request,
                    context=context,
                    registry_snapshot=registry_snapshot,
                    rejected_call_ref=call_ref,
                )
            )
        if repaired_tool_batch is not None:
            proposal = repaired_tool_batch
            concise_basis = repaired_tool_basis or (
                "recovered one or more validated Tool calls"
            )
        elif result.output_kind is ProviderOutputKind.TEXT:
            try:
                payload = parse_json_object(
                    result.assistant_text or "",
                    max_bytes=self._adapter.capabilities.max_response_bytes,
                )
                decision = self._project_decision(
                    payload=payload,
                    request=request,
                )
            except (ValidationError, TypeError, ValueError) as exc:
                result, decision = await self._repair_no_tool_decision(
                    request=request,
                    context=context,
                    registry_snapshot=registry_snapshot,
                    rejected_result_ref=result.result_ref,
                    rejected_response_hash=result.response_hash,
                    initial_validation_feedback=self._validation_feedback(exc),
                )
            proposal = decision
            concise_basis = decision.concise_basis
        else:
            proposal, concise_basis = (
                ProviderMainAgentActionProvider._proposal_from_result(result)
            )
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "provider_result_ref": result.result_ref,
            "provider_response_hash": result.response_hash,
            "provider_receipt_ref": result.provider_receipt_ref,
            "proposal": proposal.model_dump(mode="json"),
            "concise_basis": concise_basis,
        }
        return MainAgentProviderOutcome(
            **body,
            outcome_hash=canonical_hash(body),
        )

    async def _repair_invalid_tool_arguments(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
        rejected_call_ref: str,
    ) -> tuple[ProviderModelResult, ToolCallBatchAction, str]:
        """Recover one Tool decision through compact structured output once."""

        payload = {
            "request": {
                "request_ref": request.request_ref,
                "task_ref": request.task_ref,
                "turn_ref": request.turn_ref,
                "decision_sequence": request.decision_sequence,
                "origin_state_version": request.origin_state_version,
                "execution_mode": request.execution_mode.value,
                "plan_ref": request.plan_ref,
                "context_snapshot_ref": request.context_snapshot_ref,
                "visible_tool_names": list(request.visible_tool_names),
                "observation_refs": list(request.observation_refs),
            },
            "function_call_contract_repair": {
                "attempt": 1,
                "rejected_call_ref": rejected_call_ref,
                "issues": [
                    {
                        "loc": ["tool_calls", "function", "arguments"],
                        "type": "invalid_json_object",
                    }
                ],
                "instruction": (
                    "上一次响应已经选择了工具，但 Function Call Arguments 不是合法"
                    " JSON。重新选择当前仍需调用的工具，并通过本次紧凑结构化输出"
                    "返回 tool_name 和完整 arguments Object；只能选择 visible_tool_names，"
                    "不得复用、补写或猜测上一次损坏参数。"
                ),
            },
        }
        runtime_input = ProviderRuntimeInput.from_payload(
            input_ref=f"{request.request_ref}:tool-decision-repair:1",
            input_kind="main_agent_tool_decision_contract_repair",
            payload=payload,
        )
        repair_call_ref = "model-call:" + canonical_hash(
            {
                "request_ref": request.request_ref,
                "request_hash": request.request_hash,
                "repair_kind": "compact_tool_decision",
                "repair_of_call_ref": rejected_call_ref,
                "attempt": 1,
            }
        ).removeprefix("sha256:")
        repaired = await self._adapter.invoke(
            ProviderInvocationRequest(
                call_ref=repair_call_ref,
                task_ref=request.task_ref,
                state_version=request.origin_state_version,
                consumer=ContextConsumer.MAIN_AGENT,
                context=context,
                registry_snapshot=registry_snapshot,
                runtime_input=runtime_input,
                structured_output=ProviderStructuredOutputSpec.from_model(
                    schema_name="provider_tool_decision_repair",
                    output_model=ProviderToolDecisionProjection,
                    strict_mode=ProviderStrictMode.PREFERRED,
                ),
                tool_choice=ProviderToolChoice.NONE,
                tool_strict_mode=ProviderStrictMode.PREFERRED,
                max_output_tokens=min(
                    self._adapter.capabilities.max_output_tokens,
                    context.snapshot.reserved_output_tokens,
                ),
            )
        )
        if (
            repaired.output_kind is not ProviderOutputKind.STRUCTURED
            or repaired.structured_payload is None
        ):
            raise ActionLoopContractRejected(
                "compact Tool decision repair returned no structured projection"
            )
        try:
            projection = ProviderToolDecisionProjection.model_validate_json(
                canonical_json(repaired.structured_payload)
            )
        except ValidationError as exc:
            raise ActionLoopContractRejected(
                "compact Tool decision repair failed Runtime validation"
            ) from exc

        visible_names = set(registry_snapshot.visible_tool_names)
        proposals: list[ProviderToolCallProposal] = []
        for sequence, call in enumerate(projection.calls, start=1):
            if call.tool_name not in visible_names:
                raise ActionLoopContractRejected(
                    "compact Tool decision selected a non-visible Tool"
                )
            raw_arguments = canonical_json(call.arguments)
            tool_call_id = "provider-tool-call:" + canonical_hash(
                {
                    "repair_call_ref": repair_call_ref,
                    "sequence": sequence,
                    "tool_name": call.tool_name,
                    "arguments": call.arguments,
                }
            ).removeprefix("sha256:")
            proposals.append(
                ProviderToolCallProposal(
                    model_turn_ref=repair_call_ref,
                    provider_tool_call_id=tool_call_id,
                    sequence=sequence,
                    task_ref=request.task_ref,
                    context_snapshot_ref=request.context_snapshot_ref,
                    state_version=request.origin_state_version,
                    tool_name=call.tool_name,
                    raw_arguments_json=raw_arguments,
                    raw_arguments_hash=canonical_hash(raw_arguments),
                    arguments=call.arguments,
                    arguments_hash=canonical_hash(call.arguments),
                    registry_snapshot_ref=registry_snapshot.snapshot_ref,
                    registry_snapshot_hash=registry_snapshot.snapshot_hash,
                    visible_tools_hash=registry_snapshot.visible_tools_hash,
                    authorization_snapshot_ref=(
                        context.snapshot.authorization_snapshot_ref
                    ),
                )
            )
        batch = ToolCallBatchAction(
            model_turn_ref=repair_call_ref,
            calls=tuple(proposals),
        )
        return repaired, batch, projection.concise_basis

    @staticmethod
    def _is_repairable_tool_arguments_failure(
        *,
        error: ProviderAdapterError,
        request: MainAgentDecisionRequest,
        registry_snapshot: RegistrySnapshot | None,
    ) -> bool:
        return (
            error.failure.code is ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION
            and error.failure.safe_message == _INVALID_TOOL_ARGUMENTS_SAFE_MESSAGE
            and bool(request.visible_tool_names)
            and registry_snapshot is not None
            and bool(registry_snapshot.visible_tool_names)
        )

    async def _repair_no_tool_decision(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
        rejected_result_ref: str,
        rejected_response_hash: str,
        initial_validation_feedback: list[dict[str, Any]] | None = None,
    ) -> tuple[ProviderModelResult, MainAgentModelDecision]:
        """At most two structured repairs; invalid text never becomes authority."""

        base_payload: dict[str, Any] = {
            "request": request.model_dump(mode="json"),
            "rejected_result_ref": rejected_result_ref,
            "rejected_response_hash": rejected_response_hash,
            "repair_instruction": (
                "上一次无工具回答未通过 Runtime 的 Pydantic 校验。重新判断当前下一步"
                " Action，只返回满足本次精简结构化输出 Schema 的完整 JSON；不得猜测或"
                "复用无效字段。顶层必须且只能包含 action_kind、concise_basis、payload"
                " 三个唯一 key，payload 必须满足对应 action_payload_schemas。不要输出"
                " Context、state_version、引用投影或其他 Runtime 权威字段。"
            ),
            "action_payload_schemas": provider_action_payload_schemas(),
            "answer_business_rules": provider_answer_business_rules(),
            "valid_answer_example_when_evidence_is_sufficient": self._answer_example(
                context=context,
            ),
        }
        validation_feedback = initial_validation_feedback
        last_error: Exception | None = None
        for attempt in range(1, 3):
            payload = dict(base_payload)
            if validation_feedback is not None:
                payload["runtime_validation_feedback"] = {
                    "attempt": attempt,
                    "issues": validation_feedback,
                    "instruction": (
                        "按每个 loc/type 修复完整 JSON；不得删除必填业务内容，"
                        "不得改变 request 的 Context 绑定。"
                    ),
                }
            runtime_input = ProviderRuntimeInput.from_payload(
                input_ref=f"{request.request_ref}:repair:{attempt}",
                input_kind="main_agent_decision_contract_repair",
                payload=payload,
            )
            repair_call_ref = "model-call:" + canonical_hash(
                {
                    "request_ref": request.request_ref,
                    "request_hash": request.request_hash,
                    "repair_of": rejected_response_hash,
                    "attempt": attempt,
                }
            ).removeprefix("sha256:")
            try:
                repaired = await self._adapter.invoke(
                    ProviderInvocationRequest(
                        call_ref=repair_call_ref,
                        task_ref=request.task_ref,
                        state_version=request.origin_state_version,
                        consumer=ContextConsumer.MAIN_AGENT,
                        context=context,
                        registry_snapshot=registry_snapshot,
                        runtime_input=runtime_input,
                        structured_output=ProviderStructuredOutputSpec.from_model(
                            schema_name="provider_decision_projection",
                            output_model=ProviderDecisionProjection,
                            strict_mode=ProviderStrictMode.PREFERRED,
                        ),
                        tool_choice=ProviderToolChoice.NONE,
                        tool_strict_mode=ProviderStrictMode.PREFERRED,
                        max_output_tokens=min(
                            self._adapter.capabilities.max_output_tokens,
                            context.snapshot.reserved_output_tokens,
                        ),
                    )
                )
            except ProviderAdapterError as exc:
                if (
                    attempt == 1
                    and exc.failure.code
                    in {
                        ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION,
                        ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID,
                    }
                ):
                    last_error = exc
                    validation_feedback = [
                        {
                            "loc": [],
                            "type": "provider_response_contract_violation",
                        }
                    ]
                    continue
                raise
            if (
                repaired.output_kind is not ProviderOutputKind.STRUCTURED
                or repaired.structured_payload is None
            ):
                raise ActionLoopContractRejected(
                    "Main Agent structured repair returned no decision"
                )
            try:
                decision = self._project_decision(
                    payload=repaired.structured_payload,
                    request=request,
                )
                return repaired, decision
            except ValidationError as exc:
                last_error = exc
                validation_feedback = self._validation_feedback(exc)
            except (TypeError, ValueError) as exc:
                last_error = exc
                validation_feedback = [{"loc": [], "type": "invalid_json_object"}]
        safe_issue_summary = canonical_json(
            {"issues": (validation_feedback or [])[:8]}
        )
        raise ActionLoopContractRejected(
            "Main Agent structured repair failed Runtime validation: "
            f"{safe_issue_summary}"
        ) from last_error

    @staticmethod
    def _project_decision(
        *,
        payload: Mapping[str, Any],
        request: MainAgentDecisionRequest,
    ) -> MainAgentModelDecision:
        projection = ProviderDecisionProjection.model_validate_json(
            canonical_json(dict(payload))
        )
        return project_provider_decision(
            projection,
            context_snapshot_ref=request.context_snapshot_ref,
            state_version=request.origin_state_version,
        )

    @staticmethod
    def _validation_feedback(exc: Exception) -> list[dict[str, Any]]:
        if isinstance(exc, ValidationError):
            feedback: list[dict[str, Any]] = []
            for issue in exc.errors(
                include_url=False,
                include_input=False,
            )[:16]:
                item = {
                    "loc": [str(part) for part in issue.get("loc") or ()],
                    "type": str(issue.get("type") or "validation_error"),
                }
                reason_code = DeepSeekMainAgentActionProvider._safe_reason_code(issue)
                if reason_code is not None:
                    item["reason_code"] = reason_code
                feedback.append(item)
            return feedback
        return [{"loc": [], "type": "invalid_json_object"}]

    @staticmethod
    def _safe_reason_code(issue: Mapping[str, Any]) -> str | None:
        context = issue.get("ctx")
        if not isinstance(context, Mapping):
            return None
        detail = str(context.get("error") or "")
        exact = {
            "statement requires claim_type and epistemic_status": (
                "statement_fields_required"
            ),
            "limitation requires code and grounding_refs": (
                "limitation_fields_required"
            ),
            "general_advice is only valid for a recommendation": (
                "general_advice_claim_type_mismatch"
            ),
            "inference requires premise_or_trigger": "inference_premise_required",
            "project recommendation requires premise_or_trigger": (
                "recommendation_premise_required"
            ),
            "non-supported statement requires limitation_refs": (
                "statement_limitation_required"
            ),
            "unknown statement cannot claim a direct quote": (
                "unknown_statement_quote_forbidden"
            ),
            "AnswerDraft block ids must be unique": "answer_block_id_duplicate",
            "statement references an unknown limitation block": (
                "statement_limitation_ref_unknown"
            ),
            "statement/limitation links must be reciprocal": (
                "statement_limitation_link_not_reciprocal"
            ),
            "limitation references an unknown statement block": (
                "limitation_statement_ref_unknown"
            ),
            "limitation/statement links must be reciprocal": (
                "limitation_statement_link_not_reciprocal"
            ),
            "initial plan cannot declare revision reasons": (
                "initial_plan_revision_reason_forbidden"
            ),
            "replan requires a material revision reason": (
                "replan_revision_reason_required"
            ),
        }
        if detail in exact:
            return exact[detail]
        if detail.startswith("fields are not valid for block_type:"):
            return "block_type_field_mismatch"
        if detail.endswith("must be unique"):
            return "reference_values_duplicate"
        return None

    @staticmethod
    def _answer_example(
        *,
        context: ContextAssemblyResult,
    ) -> dict[str, Any] | None:
        evidence_ref = next(
            (
                entry.entry_ref
                for entry in context.projection_entries
                if entry.kind is ContextEntryKind.EVIDENCE_ATOM
            ),
            None,
        )
        if evidence_ref is None:
            return None
        return {
            "action_kind": "answer",
            "concise_basis": "当前 Context 已包含可引用 Evidence Atom。",
            "payload": {
                "response_language": "zh-CN",
                "blocks": [
                    {
                        "block_type": "statement",
                        "block_id": "statement:replace-with-unique-id",
                        "text": "用当前证据支持的准确事实替换本句。",
                        "claim_type": "fact",
                        "epistemic_status": "supported",
                        "grounding_refs": [evidence_ref],
                    }
                ],
            },
        }
