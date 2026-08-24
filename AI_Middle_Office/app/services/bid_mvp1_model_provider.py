"""Explicit OpenAI-compatible provider adapter for the controlled Model Gateway.

No request is made at import or construction time.  Provider I/O occurs only
after a durable ModelCall has been claimed by the dedicated MVP-1 worker.
"""
from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.agents.bid_assessment_local.contracts import (
    TASK_ACTION_ADAPTER,
    TASK_ACTION_SCHEMA,
)
from app.models.bid_assessment_results import BidHardGateResult, BidResolvedFact
from app.models.bid_assessment_runtime import BidAnalysisRun, BidTask
from app.models.bid_assessment_tooling import (
    BidContextManifest,
    BidToolInvocation,
    BidToolResult,
)
from app.services.bid_model_execution import ModelProviderResult
from app.services.bid_assessment_eventing import canonical_hash
from app.services.bid_task_runtime import build_task_contract
from app.services.bid_mvp1_authority import load_mvp1_fact_catalog
from app.services.bid_mvp1_retrieval_hints import retrieval_guidance_for_task
from app.services.bid_tool_context import model_visible_tool_argument_contracts


MVP1_SYSTEM_PROMPT_VERSION = (
    "bid-mvp1-controlled-task-prompt-v4-gateway-citable-candidate-filter"
)
DEEPSEEK_PROVIDER_REF = "deepseek"
DEEPSEEK_V4_FLASH_MODEL_ID = "deepseek-v4-flash"
DEEPSEEK_V4_FLASH_THINKING_MODE = "disabled"
DEEPSEEK_OFFICIAL_HOST = "api.deepseek.com"
DEEPSEEK_V4_FLASH_PRICE_VERSION = "2026-08-16"
# USD microunits per one million tokens, frozen into the Phase 4B-1 profile.
DEEPSEEK_V4_FLASH_CACHE_HIT_RATE = 2_800
DEEPSEEK_V4_FLASH_CACHE_MISS_RATE = 140_000
DEEPSEEK_V4_FLASH_OUTPUT_RATE = 280_000
MVP1_RUNTIME_TOOLS = frozenset(
    {"documents.outline", "evidence.search", "evidence.read", "facts.query"}
)


def _rfc3339_utc(value: datetime) -> str:
    """Serialize DB timestamps identically across SQLite and MySQL."""

    normalized = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return normalized.isoformat().replace("+00:00", "Z")


class BidMvp1ModelProviderError(RuntimeError):
    code = "BID_MVP1_MODEL_PROVIDER_ERROR"
    retryable = True


class BidMvp1ModelProviderConfigurationError(BidMvp1ModelProviderError):
    code = "BID_MVP1_MODEL_PROVIDER_CONFIGURATION_INVALID"
    retryable = False


def deepseek_v4_flash_cost_microunits(usage: dict[str, Any]) -> int:
    """Calculate a conservative micro-USD cost from DeepSeek usage."""

    try:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)
        raw_cache_miss = usage.get("prompt_cache_miss_tokens")
        cache_miss_tokens = (
            int(raw_cache_miss)
            if raw_cache_miss is not None
            else prompt_tokens - cache_hit_tokens
        )
    except (TypeError, ValueError) as exc:
        raise BidMvp1ModelProviderError("BID_MVP1_MODEL_USAGE_INVALID") from exc
    if (
        min(prompt_tokens, output_tokens, cache_hit_tokens, cache_miss_tokens) < 0
        or cache_hit_tokens + cache_miss_tokens != prompt_tokens
    ):
        raise BidMvp1ModelProviderError("BID_MVP1_MODEL_USAGE_INVALID")
    numerator = (
        cache_hit_tokens * DEEPSEEK_V4_FLASH_CACHE_HIT_RATE
        + cache_miss_tokens * DEEPSEEK_V4_FLASH_CACHE_MISS_RATE
        + output_tokens * DEEPSEEK_V4_FLASH_OUTPUT_RATE
    )
    return int(math.ceil(numerator / 1_000_000))


def _json_content(value: str) -> dict[str, Any]:
    content = str(value or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.S | re.I)
    if fenced:
        content = fenced.group(1)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise BidMvp1ModelProviderError("BID_MVP1_MODEL_RESPONSE_NOT_JSON") from exc
    if not isinstance(payload, dict):
        raise BidMvp1ModelProviderError("BID_MVP1_MODEL_RESPONSE_NOT_OBJECT")
    return payload


def _governed_task_action_schema(
    tool_contracts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    schema = copy.deepcopy(TASK_ACTION_ADAPTER.json_schema())
    request_tool = schema["$defs"]["RequestToolAction"]
    tool_names = sorted(tool_contracts)
    request_tool["properties"]["tool_name"] = {
        "type": "string",
        "enum": tool_names,
        "title": "Tool Name",
    }
    request_tool["allOf"] = [
        {
            "if": {
                "properties": {"tool_name": {"const": tool_name}},
                "required": ["tool_name"],
            },
            "then": {
                "properties": {
                    "arguments": copy.deepcopy(tool_contracts[tool_name])
                }
            },
        }
        for tool_name in tool_names
    ]
    return schema


def _normalize_control_plane_fields(
    action: dict[str, Any],
    request_envelope: dict[str, Any],
    *,
    citable_evidence_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Keep opaque idempotency identifiers under Gateway authority."""

    normalized = copy.deepcopy(action)
    if (
        str(normalized.get("action_type") or "") == "submit_fact_candidates"
        and citable_evidence_ids is not None
    ):
        allowed = set(str(value) for value in citable_evidence_ids)
        accepted_candidates: list[dict[str, Any]] = []
        dropped_count = 0
        for raw_candidate in list(normalized.get("candidates") or []):
            candidate = dict(raw_candidate) if isinstance(raw_candidate, dict) else {}
            evidence_ids = candidate.get("evidence_ids")
            evidence_id_values = (
                [str(value) for value in evidence_ids]
                if isinstance(evidence_ids, list)
                else []
            )
            if (
                evidence_id_values
                and len(evidence_id_values) == len(set(evidence_id_values))
                and set(evidence_id_values) <= allowed
            ):
                accepted_candidates.append(candidate)
            else:
                dropped_count += 1
        normalized["candidates"] = accepted_candidates
        if dropped_count:
            reason_codes = list(dict.fromkeys(normalized.get("reason_codes") or []))
            drop_code = "GATEWAY_DROPPED_UNCITABLE_FACT_CANDIDATE"
            if drop_code not in reason_codes:
                reason_codes.append(drop_code)
            normalized["reason_codes"] = reason_codes[:20]
    if (
        str(normalized.get("action_type") or "")
        in {"submit_fact_candidates", "submit_claim_candidates"}
        and isinstance(normalized.get("candidates"), list)
        and not normalized["candidates"]
    ):
        reason_codes = list(dict.fromkeys(normalized.get("reason_codes") or []))
        if "EVIDENCE_INSUFFICIENT" not in reason_codes:
            reason_codes.append("EVIDENCE_INSUFFICIENT")
        return {
            "action_type": "finish",
            "completion_summary": "No governed candidate is supported by the available evidence.",
            "output_candidate": None,
            "reason_codes": reason_codes[:20],
        }
    if str(normalized.get("action_type") or "") == "submit_fact_candidates":
        for candidate in list(normalized.get("candidates") or []):
            if (
                not isinstance(candidate, dict)
                or candidate.get("value_type") != "money"
                or not isinstance(candidate.get("value"), dict)
                or candidate["value"].get("currency") != "CNY"
            ):
                continue
            raw_amount = candidate["value"].get("amount")
            if isinstance(raw_amount, bool) or not isinstance(
                raw_amount, (str, int, float)
            ):
                continue
            try:
                amount = Decimal(str(raw_amount))
            except InvalidOperation:
                continue
            if (
                not amount.is_finite()
                or amount < 0
                or amount.as_tuple().exponent < -4
            ):
                continue
            candidate["value"]["amount"] = format(
                amount.quantize(Decimal("0.0000")),
                "f",
            )
        return normalized
    if str(normalized.get("action_type") or "") != "request_tool":
        return normalized
    arguments = dict(normalized.get("arguments") or {})
    if str(normalized.get("tool_name") or "") == "evidence.search":
        # These keys describe query construction to the model; they are not
        # Evidence MCP arguments and must never cross the Tool Gateway.
        for hint_key in (
            "field_aliases",
            "primary_query",
            "query_language_policy",
            "no_result_policy",
        ):
            arguments.pop(hint_key, None)
        normalized["arguments"] = arguments
    normalized["tool_call_id"] = "tc_" + canonical_hash(
        {
            "model_call_id": str(request_envelope.get("model_call_id") or ""),
            "action_seq": request_envelope.get("action_seq"),
            "tool_name": normalized.get("tool_name"),
            "arguments": normalized.get("arguments"),
        }
    )[:32]
    return normalized


class ControlledChatCompletionsProvider:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        provider_ref: str,
        model_ref: str,
        api_key: str,
        chat_url: str,
        thinking_mode: str = DEEPSEEK_V4_FLASH_THINKING_MODE,
        timeout_seconds: int = 120,
    ):
        self._session_factory = session_factory
        self._provider_ref = str(provider_ref)
        self._model_ref = str(model_ref)
        self._api_key = str(api_key)
        self._chat_url = str(chat_url)
        self._thinking_mode = str(thinking_mode)
        self._timeout_seconds = max(30, min(int(timeout_seconds), 900))
        endpoint = urlsplit(self._chat_url)
        if (
            self._provider_ref != DEEPSEEK_PROVIDER_REF
            or self._model_ref != DEEPSEEK_V4_FLASH_MODEL_ID
            or self._thinking_mode != DEEPSEEK_V4_FLASH_THINKING_MODE
            or not self._api_key
            or endpoint.scheme != "https"
            or endpoint.hostname != DEEPSEEK_OFFICIAL_HOST
            or endpoint.username
            or endpoint.password
            or endpoint.path not in {"/chat/completions", "/v1/chat/completions"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise BidMvp1ModelProviderConfigurationError(
                "BID_MVP1_MODEL_PROVIDER_CONFIGURATION_INVALID"
            )

    def _prompt_context(self, envelope: dict[str, Any]) -> dict[str, Any]:
        db = self._session_factory()
        try:
            context = db.query(BidContextManifest).filter(
                BidContextManifest.id == str(envelope["context_manifest_id"]),
                BidContextManifest.manifest_hash == str(envelope["context_manifest_hash"]),
            ).one()
            task = db.query(BidTask).filter(BidTask.id == str(envelope["task_id"])).one()
            run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == str(envelope["run_id"])).one()
            manifest = dict(context.manifest_json or {})
            tool_result_ids = list(manifest.get("included_tool_result_ids") or [])
            results = []
            if tool_result_ids:
                rows = (
                    db.query(BidToolResult, BidToolInvocation)
                    .join(BidToolInvocation, BidToolInvocation.id == BidToolResult.invocation_id)
                    .filter(
                        BidToolResult.id.in_(tuple(tool_result_ids)),
                        BidToolInvocation.run_id == run.id,
                        BidToolInvocation.task_id == task.id,
                    )
                    .order_by(BidToolResult.created_at.asc())
                    .all()
                )
                results = [
                    {
                        "result_id": str(result.id),
                        "tool_name": str(invocation.tool_name),
                        "status": str(result.status),
                        "data": result.inline_data_json if result.storage_kind == "inline" else {},
                        "evidence_refs": list(result.evidence_refs_json or []),
                        "citable_evidence_ids": (
                            list(result.evidence_refs_json or [])
                            if str(invocation.tool_name) == "evidence.read"
                            else []
                        ),
                    }
                    for result, invocation in rows
                ]
            fact_ids = list(manifest.get("included_fact_ids") or [])
            facts = []
            if fact_ids:
                facts = [
                    {
                        "fact_id": str(row.id),
                        "fact_slot": str(row.fact_slot),
                        "status": str(row.status),
                        "value_type": row.value_type,
                        "value": row.value_json,
                    }
                    for row in db.query(BidResolvedFact)
                    .filter(BidResolvedFact.run_id == run.id, BidResolvedFact.id.in_(tuple(fact_ids)))
                    .order_by(BidResolvedFact.fact_slot.asc())
                    .all()
                ]
            gate_ids = list(manifest.get("included_gate_ids") or [])
            gates = []
            if gate_ids:
                gates = [
                    {
                        "gate_id": str(row.id),
                        "gate_code": str(row.gate_code),
                        "status": str(row.status),
                        "severity": str(row.severity),
                        "reason_codes": list(row.reason_codes_json or []),
                    }
                    for row in db.query(BidHardGateResult)
                    .filter(BidHardGateResult.run_id == run.id, BidHardGateResult.id.in_(tuple(gate_ids)))
                    .order_by(BidHardGateResult.gate_code.asc())
                    .all()
                ]
            task_contract = build_task_contract(db, task)
            allowed_tools = list(task_contract["allowed_tools"])
            fact_contracts = [
                {
                    "fact_slot": str(item["slot"]),
                    "allowed_value_types": list(item.get("value_types") or []),
                }
                for item in load_mvp1_fact_catalog()["slots"]
                if str(item.get("task_type")) == str(task.task_type)
            ]
            return {
                "run": {
                    "run_id": str(run.id),
                    "assessment_id": str(run.assessment_id),
                    "scope_id": str(run.scope_id),
                    "evaluation_time": _rfc3339_utc(run.evaluation_time),
                },
                "task": {
                    "task_id": str(task.id),
                    "task_type": str(task.task_type),
                    "objective": str(task.objective),
                    "completion_contract": str(task.completion_contract),
                },
                "fact_scope": {
                    "assessment_id": str(run.assessment_id),
                    "lot_id": task_contract["scope"].get("lot_id"),
                },
                "allowed_tools": allowed_tools,
                "runtime_tools": [
                    tool_name
                    for tool_name in allowed_tools
                    if tool_name in MVP1_RUNTIME_TOOLS
                ],
                "allowed_fact_slots": [
                    item["fact_slot"] for item in fact_contracts
                ],
                "fact_candidate_contracts": fact_contracts,
                "tool_results": results,
                "resolved_facts": facts,
                "hard_gates": gates,
                "retrieval_guidance": retrieval_guidance_for_task(
                    str(task.task_type)
                ),
                "evaluation_time_utc": _rfc3339_utc(run.evaluation_time),
            }
        finally:
            db.close()

    def execute(
        self,
        *,
        request_envelope: dict[str, Any],
        provider_request_id: str,
    ) -> ModelProviderResult:
        if str(request_envelope.get("provider_ref")) != self._provider_ref:
            raise BidMvp1ModelProviderConfigurationError(
                "BID_MVP1_MODEL_PROVIDER_REF_NOT_ALLOWED"
            )
        if str(request_envelope.get("model_ref")) != self._model_ref:
            raise BidMvp1ModelProviderConfigurationError(
                "BID_MVP1_MODEL_REF_NOT_ALLOWED"
            )
        context = self._prompt_context(request_envelope)
        tool_contracts = model_visible_tool_argument_contracts(
            context["runtime_tools"]
        )
        system = (
            "你是旗胜投标机会研判 Agent 的单 Task 受控执行器。只输出一个 JSON 对象，"
            "不得输出 Markdown 或解释。你不能直接决定 Run 成败，不能伪造事实或证据。"
            "输出必须严格匹配 output_contract.json_schema。每次只选择一个 action_type 分支，"
            "只能输出该分支定义的字段；禁止携带其他分支字段，禁止用 null 或空对象填充"
            "未选择分支的字段。"
            "需要资料时只能从 runtime_tools 选择 request_tool；allowed_tools 只是合同上限。"
            "request_tool.arguments 必须严格匹配 tool_argument_contracts 中对应工具的 Schema，"
            "不得添加 Schema 未声明字段。assessment_id、lot_id、scope_id 等 Scope 由服务端"
            "注入，除非工具 Schema 明确声明，否则禁止放入 arguments。"
            "tool_call_id 是非业务幂等占位符，Gateway 会在接收响应后用稳定 ASCII ID 覆盖它；"
            "若 governed_context.retrieval_guidance 存在，检索应优先使用文档语言、"
            "primary_query 和 field_aliases；no_result 后只能按该策略改写检索或无事实结束，"
            "这些字段只是构造 query 的提示，绝不能作为 arguments 的字段；"
            "不得改用 runtime_tools 之外的工具或编造事实。"
            "检索后对用于事实的证据必须先"
            "调用 evidence.read；只有 evidence.read 返回且 context_read=true 的 evidence_id 才能"
            "写入 document 类型 FactAssertionCandidate。事实提取任务使用 submit_fact_candidates；"
            "FactAssertionCandidate.evidence_ids 必须且只能从 evidence.read 结果顶层的"
            "citable_evidence_ids 逐字选择；禁止使用 evidence.search hits 的 Child ID、"
            "evidence.read 请求 arguments 中的 ID 或自行改写 ID。"
            "同一 Task 内严禁以相同 arguments 重复调用同一工具；tool_results 已包含的"
            "evidence_id 严禁再次 evidence.read。事实提取任务完成一次 evidence.search 和一次"
            "evidence.read 后，必须在当前动作提交可支持的 fact candidates；若现有可引用证据"
            "仍不足，则必须 finish 并用 EVIDENCE_INSUFFICIENT 标明无事实结束，禁止继续循环检索。"
            "fact_scope 是事实 scope 的唯一取值来源：scope.type=assessment 时 scope.id 使用"
            "assessment_id；scope.type=lot 时 scope.id 使用 lot_id。禁止把内部 scope_id 当作"
            "业务标段 id。asserted_at 必须逐字使用 evaluation_time_utc。"
            "submit_fact_candidates.candidates 中每个对象必须且只能包含 fact_slot、value、"
            "value_type、scope、source_type、evidence_ids、confidence、asserted_at 八个字段；"
            "fact_slot 和 value_type 必须匹配 fact_candidate_contracts。多个同类要求应合并到"
            "同一个 requirement_list/clause_list 候选，不要为同一 fact_slot 重复造候选；"
            "reason_codes 必须去重。value_type=money 时 value 必须严格为"
            "{\"amount\":\"10000.0000\",\"currency\":\"CNY\"} 形状，amount 是"
            "非负十进制字符串且恰好四位小数；无法可靠换算时改用允许的 text 类型。"
            "requirement_list/clause_list/scoring_item_list/deliverable_list 必须使用 JSON 数组；"
            "当 fact_candidate_contracts 允许 project_identity 时，项目概况优先输出对象，"
            "只使用有证据的 project_name、procurer_name、owner_name、client_name 字段；"
            "资格 requirement_list 的对象应使用 requirement_type（qualification、safety_license、"
            "performance、personnel）以及有原文支持的 code/name，禁止用关键词猜类别；"
            "工期约束只有原文能换算投标准备工作量时才使用 required_bid_person_days，"
            "否则保留原文结构且不得估算；"
            "datetime 必须是以 Z 结尾的 RFC3339 字符串，date 必须是 YYYY-MM-DD。"
            "Gateway 只会对可无损解析的 CNY 数字金额补齐四位小数，不会猜测或换算不确定金额。"
            "synthesize_assessment 使用 submit_claim_candidates；没有可提交内容时使用 finish。"
            "request_tool 必须包含 action_type、tool_call_id、tool_name、arguments、reason_codes。"
            "finish 必须包含 action_type、completion_summary、reason_codes，可选 output_candidate；"
            "finish 严禁包含 tool_call_id、tool_name 或 arguments。"
            "不要输出思维链，只输出可审计结果。"
        )
        user = json.dumps(
            {
                "request": {
                    "system_prompt_version": MVP1_SYSTEM_PROMPT_VERSION,
                    "action_seq": request_envelope.get("action_seq"),
                    "logical_role": request_envelope.get("logical_role"),
                    "input_token_limit": request_envelope.get("input_token_limit"),
                    "output_token_limit": request_envelope.get("output_token_limit"),
                },
                "output_contract": {
                    "schema_id": TASK_ACTION_SCHEMA,
                    "exclusive_action_branch": True,
                    "omit_unselected_branch_fields": True,
                    "finish_example": {
                        "action_type": "finish",
                        "completion_summary": "No governed action is required.",
                        "output_candidate": None,
                        "reason_codes": ["NO_ACTION_REQUIRED"],
                    },
                    "json_schema": _governed_task_action_schema(tool_contracts),
                },
                "tool_argument_contracts": tool_contracts,
                "governed_context": context,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        try:
            response = httpx.post(
                self._chat_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "X-Idempotency-Key": str(provider_request_id),
                },
                json={
                    "model": self._model_ref,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "thinking": {"type": self._thinking_mode},
                    "temperature": 0,
                    "max_tokens": int(request_envelope["output_token_limit"]),
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            if str(body.get("model") or "") != self._model_ref:
                raise BidMvp1ModelProviderError("BID_MVP1_MODEL_RESPONSE_MODEL_MISMATCH")
            choice = body["choices"][0]
            action = _normalize_control_plane_fields(
                _json_content(choice["message"]["content"]),
                request_envelope,
                citable_evidence_ids={
                    str(evidence_id)
                    for tool_result in context.get("tool_results") or []
                    for evidence_id in tool_result.get("citable_evidence_ids") or []
                },
            )
            usage = dict(body.get("usage") or {})
            input_tokens = int(usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or 0)
            return ModelProviderResult(
                action=action,
                usage={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
                finish_reason=str(choice.get("finish_reason") or "stop")[:64],
                provider_receipt_id=str(body.get("id") or provider_request_id)[:191],
                actual_cost_microunits=deepseek_v4_flash_cost_microunits(usage),
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BidMvp1ModelProviderError("BID_MVP1_MODEL_PROVIDER_REQUEST_FAILED") from exc
