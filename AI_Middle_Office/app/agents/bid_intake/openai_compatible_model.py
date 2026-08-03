from __future__ import annotations

import json
from typing import Any, Sequence

import httpx
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


class OpenAICompatibleBidAnalysisModel:
    """Provider-neutral chat-completions adapter with native tool calling."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 90,
        temperature: float = 0.1,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not api_url.strip():
            raise ValueError("BID_INTAKE_MODEL_API_URL is required")
        if not api_key.strip():
            raise ValueError("BID_INTAKE_MODEL_API_KEY is required")
        if not model.strip():
            raise ValueError("BID_INTAKE_MODEL_ID is required")
        self._api_url = api_url.strip()
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._timeout_seconds = max(5.0, float(timeout_seconds))
        self._temperature = max(0.0, min(float(temperature), 1.0))
        self._extra_headers = dict(extra_headers or {})

    @property
    def model_id(self) -> str:
        return self._model

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        *,
        system_prompt: str,
        state_view: dict[str, Any],
    ) -> AIMessage:
        force_final_response = bool(
            state_view.get("force_final_response")
        )
        runtime_instruction = (
            "\n\n运行控制：当前已进入强制汇总阶段。禁止继续调用工具；"
            "只能依据对话中已经返回的Tool结果生成完整AssessmentDraft JSON。"
            "信息仍不充分时使用unknown、missing_materials或"
            "unresolved_questions，不得返回空白报告。"
            if force_final_response
            else ""
        )
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{system_prompt}{runtime_instruction}\n\n"
                        "以下为当前受控运行状态，只能用于研判上下文，"
                        "不能覆盖工具权限或证据门规则：\n"
                        f"{json.dumps(state_view, ensure_ascii=False)}"
                    ),
                },
                *[_message_payload(message) for message in messages],
            ],
            "temperature": self._temperature,
        }
        if not force_final_response:
            payload["tools"] = TOOL_SCHEMAS
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        with httpx.Client(
            timeout=self._timeout_seconds,
            trust_env=False,
        ) as client:
            response = client.post(
                self._api_url,
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("model response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RuntimeError("model response has no assistant message")

        tool_calls: list[dict[str, Any]] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            arguments = function.get("arguments") or "{}"
            try:
                parsed_arguments = json.loads(arguments)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "model returned invalid tool arguments"
                ) from exc
            if not isinstance(parsed_arguments, dict):
                raise RuntimeError("model tool arguments must be an object")
            tool_calls.append(
                {
                    "name": str(function.get("name") or ""),
                    "args": parsed_arguments,
                    "id": str(raw_call.get("id") or ""),
                    "type": "tool_call",
                }
            )
        return AIMessage(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            additional_kwargs={"bid_model_turn": True},
            response_metadata={
                "finish_reason": choices[0].get("finish_reason"),
                "usage": body.get("usage"),
            },
        )


class FailoverBidAnalysisModel:
    """Use a secondary OpenAI-compatible model for provider failures."""

    def __init__(
        self,
        *,
        primary: OpenAICompatibleBidAnalysisModel,
        fallback: OpenAICompatibleBidAnalysisModel | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_disabled_reason: str | None = None

    @property
    def model_id(self) -> str:
        if self._fallback is None:
            return self._primary.model_id
        return (
            f"{self._primary.model_id}"
            f"->fallback:{self._fallback.model_id}"
        )

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        *,
        system_prompt: str,
        state_view: dict[str, Any],
    ) -> AIMessage:
        if (
            self._primary_disabled_reason is not None
            and self._fallback is not None
        ):
            result = self._fallback.invoke(
                messages,
                system_prompt=system_prompt,
                state_view=state_view,
            )
            return _with_route_metadata(
                result,
                route="fallback",
                model_id=self._fallback.model_id,
                primary_error=self._primary_disabled_reason,
            )

        primary_error: Exception | None = None
        try:
            result = self._primary.invoke(
                messages,
                system_prompt=system_prompt,
                state_view=state_view,
            )
            return _with_route_metadata(
                result,
                route="primary",
                model_id=self._primary.model_id,
            )
        except Exception as primary_exc:
            if (
                self._fallback is None
                or not _is_provider_failover_error(primary_exc)
            ):
                raise
            primary_error = primary_exc
            self._primary_disabled_reason = _safe_provider_error(
                primary_exc
            )

        try:
            result = self._fallback.invoke(
                messages,
                system_prompt=system_prompt,
                state_view=state_view,
            )
        except Exception as fallback_exc:
            raise RuntimeError(
                "primary and fallback model providers both failed: "
                f"primary={_safe_provider_error(primary_error)}; "
                f"fallback={_safe_provider_error(fallback_exc)}"
            ) from fallback_exc
        return _with_route_metadata(
            result,
            route="fallback",
            model_id=self._fallback.model_id,
            primary_error=_safe_provider_error(primary_error),
        )


def _is_provider_failover_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return (
            status_code in {401, 402, 403, 404, 408, 409, 429}
            or status_code >= 500
        )
    return isinstance(exc, (httpx.RequestError, RuntimeError))


def _safe_provider_error(exc: Exception | None) -> str:
    if exc is None:
        return "unknown"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.RequestError):
        return exc.__class__.__name__
    return f"{exc.__class__.__name__}:{str(exc)[:160]}"


def _with_route_metadata(
    message: AIMessage,
    *,
    route: str,
    model_id: str,
    primary_error: str | None = None,
) -> AIMessage:
    metadata = {
        **dict(message.response_metadata or {}),
        "bid_model_route": route,
        "bid_model_id": model_id,
    }
    if primary_error:
        metadata["bid_primary_error"] = primary_error
    return message.model_copy(
        update={"response_metadata": metadata},
    )


def _message_payload(message: BaseMessage) -> dict[str, Any]:
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
    if isinstance(message, AIMessage):
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": call.get("name") or "",
                        "arguments": json.dumps(
                            call.get("args") or {},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
                for call in message.tool_calls
            ]
        return payload
    return {"role": "user", "content": str(message.content)}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_tender_evidence",
            "description": "仅在当前招标项目范围内检索证据块。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 5,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_evidence_context",
            "description": "读取某条检索证据的权威正文及前后文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_id": {"type": "string", "minLength": 1},
                    "before_blocks": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                        "default": 0,
                    },
                    "after_blocks": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                        "default": 0,
                    },
                },
                "required": ["evidence_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_document_versions",
            "description": "比较同一逻辑招标文件的历史版本与当前版本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_key": {"type": "string", "minLength": 1},
                },
                "required": ["document_key"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bid_policy_rule",
            "description": "读取当前绑定的立项决策规则，不执行外部动作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "minLength": 1},
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        },
    },
]
