"""V603 synthetic Planner/Intent model evaluation.

Only versioned synthetic cases are sent. The evaluator never reads bid files,
databases, RAG sources, Tools, or MCP and never prints credentials/raw replies.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.bid_assessment_pure.complexity_gate import DefaultComplexityGate
from app.agents.bid_assessment_pure.planning import IntentUnderstanding, TaskPlan
from app.agents.bid_assessment_pure.registry import build_initial_registry
from app.agents.bid_assessment_pure.state_machine import create_running_task


DATASET_PATH = PROJECT_ROOT / "evals" / "bid_assessment" / "v603-synthetic-cases.json"
PROMPT_VERSION = "bid-pure-agent-v603-synthetic-v2"
DEFAULT_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
ALLOWED_HOSTS = frozenset({"api.deepseek.com"})


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    chat_url: str
    model: str
    timeout_seconds: int


class SafeEvaluationError(RuntimeError):
    pass


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic V603 model evaluation.")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--domain",
        choices=("all", "intent", "planner"),
        default="all",
        help="Run all cases or one evaluation domain for bounded calibration.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Optionally run one or more named synthetic cases.",
    )
    parser.add_argument(
        "--secret-env-file",
        type=Path,
        help=(
            "Optional explicit file containing only BID_ASSESSMENT_MODEL_API_KEY "
            "or DEEPSEEK_API_KEY; values are never printed."
        ),
    )
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    return parser.parse_args()


def _secret_values(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    allowed_names = {
        "BID_ASSESSMENT_MODEL_API_KEY",
        "DEEPSEEK_API_KEY",
        "BID_ASSESSMENT_MODEL_CHAT_URL",
        "DEEPSEEK_CHAT_URL",
        "BID_ASSESSMENT_MODEL_ID",
        "DEEPSEEK_MODEL",
    }
    values: dict[str, str] = {}
    for raw_line in path.resolve().read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if name in allowed_names:
            values[name] = raw_value.strip().strip('"').strip("'")
    return values


def _model_config(
    *,
    timeout_seconds: int,
    secret_env_file: Path | None = None,
) -> ModelConfig | None:
    secrets = _secret_values(secret_env_file)

    def configured(name: str) -> str:
        return os.getenv(name, "").strip() or secrets.get(name, "").strip()

    api_key = (
        configured("BID_ASSESSMENT_MODEL_API_KEY")
        or configured("DEEPSEEK_API_KEY")
    )
    if not api_key:
        return None
    chat_url = (
        configured("BID_ASSESSMENT_MODEL_CHAT_URL")
        or configured("DEEPSEEK_CHAT_URL")
        or DEFAULT_CHAT_URL
    )
    endpoint = urlsplit(chat_url)
    if endpoint.scheme != "https" or endpoint.hostname not in ALLOWED_HOSTS:
        raise SafeEvaluationError("V603 requires the official HTTPS DeepSeek endpoint")
    if endpoint.path not in {"/chat/completions", "/v1/chat/completions"}:
        raise SafeEvaluationError("V603 DeepSeek endpoint path is not allowed")
    model = (
        configured("BID_ASSESSMENT_MODEL_ID")
        or configured("DEEPSEEK_MODEL")
        or DEFAULT_MODEL
    )
    return ModelConfig(
        api_key=api_key,
        chat_url=chat_url,
        model=model,
        timeout_seconds=max(30, min(timeout_seconds, 300)),
    )


def _load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "bid.pure_agent.v603.eval.v1":
        raise SafeEvaluationError("unsupported V603 dataset schema")
    if payload.get("dataset_kind") != "synthetic_only":
        raise SafeEvaluationError("V603 accepts synthetic-only datasets")
    if not payload.get("intent_cases") or not payload.get("planner_cases"):
        raise SafeEvaluationError("V603 dataset must contain intent and planner cases")
    return payload


def _json_object(raw: str) -> dict[str, Any]:
    content = str(raw or "").strip()
    if content.startswith("```") and content.endswith("```"):
        content = content[3:-3].strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SafeEvaluationError("model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SafeEvaluationError("model response was not a JSON object")
    return payload


def _optional_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


async def _call_json(
    client: httpx.AsyncClient,
    *,
    config: ModelConfig,
    system_prompt: str,
    user_payload: dict[str, Any],
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, int | None]]:
    response = await client.post(
        config.chat_url,
        headers={"Authorization": f"Bearer {config.api_key}"},
        json={
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True)},
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        },
    )
    if response.status_code in {401, 403}:
        raise SafeEvaluationError("model authentication was rejected")
    if response.status_code == 429:
        raise SafeEvaluationError("model rate limit was reached")
    if response.status_code >= 400:
        raise SafeEvaluationError(f"model request failed with HTTP {response.status_code}")
    try:
        envelope = response.json()
        content = envelope["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise SafeEvaluationError("model response envelope was invalid") from exc
    usage = envelope.get("usage") if isinstance(envelope, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return _json_object(content), {
        "input_tokens": _optional_non_negative_int(usage.get("prompt_tokens")),
        "output_tokens": _optional_non_negative_int(usage.get("completion_tokens")),
        "total_tokens": _optional_non_negative_int(usage.get("total_tokens")),
    }


def _intent_prompt() -> str:
    return (
        "你是投标机会研判主 Agent 的意图与信息需求理解能力，不是固定标签分类器。"
        "只基于当前用户问题与 available_context 形成开放理解；不得编造资料内容。"
        "available_context 表示对应资料入口已经完成授权绑定，后续可以由 Agent 使用工具检索；"
        "只要所需来源已出现在 available_context 中，就不得因为尚未读取具体内容而要求用户重复上传。"
        "只有完成当前目标所需的不可替代来源没有出现在 available_context 中，才设置"
        " clarification_needed=true。缺少招标资料时 blocking_slot_name 必须逐字返回"
        " assessment.documents；除此之外不得使用近义 Slot 名。"
        "简单单一问题建议 direct；跨招标资料和企业知识、多信息需求或多阶段综合判断"
        "建议 planned。只返回符合以下 JSON Schema 的 JSON 对象，不输出思维链或 Markdown："
        + json.dumps(IntentUnderstanding.model_json_schema(), ensure_ascii=False, sort_keys=True)
    )


def _planner_prompt(*, visible_tools: list[dict[str, Any]]) -> str:
    return (
        "你是投标机会研判主 Agent 内部的有限滚动 Planner，不是 Workflow 编排器。"
        "根据当前目标、IntentUnderstanding 和动态可见工具生成当下必要的有限计划。"
        "步骤依赖必须无环；tool_hint 只能为空或引用 visible_tools.name；不要执行工具，"
        "不要创建固定阶段，不要把 slot 塞进 steps。expected_output 用自然语言，"
        "output_schema 用 JSON Schema 约束。只返回符合以下 JSON Schema 的 JSON 对象，"
        "不输出思维链或 Markdown。TaskPlan Schema="
        + json.dumps(TaskPlan.model_json_schema(), ensure_ascii=False, sort_keys=True)
        + "；visible_tools="
        + json.dumps(visible_tools, ensure_ascii=False, sort_keys=True)
    )


def _visible_tool_contracts(names: list[str]) -> list[dict[str, Any]]:
    registry = build_initial_registry()
    contracts: list[dict[str, Any]] = []
    for name in names:
        contract = registry.get(name).model_visible_contract()
        contracts.append({
            "name": contract.name,
            "description": contract.description,
            "input_schema": contract.input_schema,
        })
    return contracts


def _score_intent(case: dict[str, Any], understanding: IntentUnderstanding) -> dict[str, Any]:
    expected = case["expected"]
    task = create_running_task(
        task_id=f"task:{case['id']}",
        session_id="conversation:v603-eval",
        goal_ref=f"goal:{case['id']}",
    )
    final_mode = DefaultComplexityGate().decide(
        task=task, understanding=understanding
    ).execution_mode.value
    required_sources = set(expected.get("required_source_hints") or [])
    actual_sources = {item.value for item in understanding.source_hints}
    checks = {
        "final_execution_mode": final_mode == expected["final_execution_mode"],
        "clarification_needed": understanding.clarification_needed == expected["clarification_needed"],
        "blocking_slot_name": "blocking_slot_name" not in expected or understanding.blocking_slot_name == expected["blocking_slot_name"],
        "required_source_hints": required_sources <= actual_sources,
        "minimum_information_needs": len(understanding.information_needs) >= int(expected.get("minimum_information_needs") or 0),
    }
    return {
        "case_id": case["id"],
        "passed": all(checks.values()),
        "checks": checks,
        "final_execution_mode": final_mode,
        "clarification_needed": understanding.clarification_needed,
        "blocking_slot_name": understanding.blocking_slot_name,
        "source_hints": [item.value for item in understanding.source_hints],
        "information_need_count": len(understanding.information_needs),
    }


def _score_plan(case: dict[str, Any], plan: TaskPlan) -> dict[str, Any]:
    expected = case["expected"]
    visible = set(case["visible_tool_names"])
    plan.validate_tool_hints(visible)
    hints = {step.tool_hint for step in plan.steps if step.tool_hint is not None}
    checks = {
        "minimum_steps": len(plan.steps) >= int(expected["minimum_steps"]),
        "maximum_steps": len(plan.steps) <= int(expected["maximum_steps"]),
        "tool_hints_visible": hints <= visible,
        "required_tool_hints": set(expected.get("required_tool_hints") or []) <= hints,
        "next_decision_resolved": plan.next_decision.step_id is not None,
    }
    return {
        "case_id": case["id"],
        "passed": all(checks.values()),
        "checks": checks,
        "step_count": len(plan.steps),
        "tool_hints": sorted(hints),
    }


def _failed_case(case_id: str, exc: Exception) -> dict[str, Any]:
    return {"case_id": case_id, "passed": False, "error_type": type(exc).__name__}


def _accumulate_usage(aggregate: dict[str, int], usage: dict[str, int | None]) -> None:
    for key in aggregate:
        if usage[key] is not None:
            aggregate[key] += int(usage[key])


async def _evaluate(
    dataset: dict[str, Any],
    config: ModelConfig,
    *,
    domain: str = "all",
    case_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    intent_results: list[dict[str, Any]] = []
    planner_results: list[dict[str, Any]] = []
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    call_count = 0
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        intent_cases = dataset["intent_cases"] if domain in {"all", "intent"} else ()
        if case_ids:
            intent_cases = tuple(case for case in intent_cases if case["id"] in case_ids)
        for case in intent_cases:
            try:
                payload, usage = await _call_json(
                    client,
                    config=config,
                    system_prompt=_intent_prompt(),
                    user_payload={"user_message": case["user_message"], "available_context": case["available_context"]},
                    max_tokens=1_024,
                )
                call_count += 1
                result = _score_intent(case, IntentUnderstanding.model_validate(payload))
                _accumulate_usage(total_usage, usage)
            except (SafeEvaluationError, httpx.HTTPError):
                raise
            except Exception as exc:
                result = _failed_case(case["id"], exc)
            intent_results.append(result)
        planner_cases = dataset["planner_cases"] if domain in {"all", "planner"} else ()
        if case_ids:
            planner_cases = tuple(case for case in planner_cases if case["id"] in case_ids)
        for case in planner_cases:
            try:
                visible_tools = _visible_tool_contracts(case["visible_tool_names"])
                payload, usage = await _call_json(
                    client,
                    config=config,
                    system_prompt=_planner_prompt(visible_tools=visible_tools),
                    user_payload={
                        "user_message": case["user_message"],
                        "understanding": case["understanding"],
                        "visible_tool_names": case["visible_tool_names"],
                    },
                    max_tokens=2_048,
                )
                call_count += 1
                result = _score_plan(case, TaskPlan.model_validate(payload))
                _accumulate_usage(total_usage, usage)
            except (SafeEvaluationError, httpx.HTTPError):
                raise
            except Exception as exc:
                result = _failed_case(case["id"], exc)
            planner_results.append(result)
    intent_passed = sum(bool(item["passed"]) for item in intent_results)
    planner_passed = sum(bool(item["passed"]) for item in planner_results)
    passed = intent_passed == len(intent_results) and planner_passed == len(planner_results)
    return {
        "schema_version": "bid.pure_agent.v603.result.v1",
        "status": "passed" if passed else "failed",
        "prompt_version": PROMPT_VERSION,
        "provider_ref": "deepseek",
        "model_ref": config.model,
        "dataset_kind": "synthetic_only",
        "evaluation_domain": domain,
        "evaluated_case_ids": [
            item["case_id"] for item in (*intent_results, *planner_results)
        ],
        "business_data_included": False,
        "call_count": call_count,
        "usage": total_usage,
        "intent": {"passed": intent_passed, "total": len(intent_results), "cases": intent_results},
        "planner": {"passed": planner_passed, "total": len(planner_results), "cases": planner_results},
    }


def main() -> int:
    args = _arguments()
    try:
        dataset = _load_dataset(args.dataset.resolve())
        requested_case_ids = frozenset(args.case_id)
        known_case_ids = {
            case["id"]
            for case in (*dataset["intent_cases"], *dataset["planner_cases"])
        }
        unknown_case_ids = sorted(requested_case_ids - known_case_ids)
        if unknown_case_ids:
            raise SafeEvaluationError(
                f"unknown V603 case ids: {', '.join(unknown_case_ids)}"
            )
        selected_intent_count = sum(
            1
            for case in dataset["intent_cases"]
            if args.domain in {"all", "intent"}
            and (not requested_case_ids or case["id"] in requested_case_ids)
        )
        selected_planner_count = sum(
            1
            for case in dataset["planner_cases"]
            if args.domain in {"all", "planner"}
            and (not requested_case_ids or case["id"] in requested_case_ids)
        )
        if selected_intent_count + selected_planner_count == 0:
            raise SafeEvaluationError("V603 selection contains no runnable cases")
        config = _model_config(
            timeout_seconds=args.timeout_seconds,
            secret_env_file=args.secret_env_file,
        )
        if args.preflight:
            print(json.dumps({
                "status": "ready" if config else "model_configuration_missing",
                "schema_version": dataset["schema_version"],
                "dataset_kind": dataset["dataset_kind"],
                "intent_cases": len(dataset["intent_cases"]),
                "planner_cases": len(dataset["planner_cases"]),
                "planned_model_calls": (
                    selected_intent_count + selected_planner_count
                ),
                "evaluation_domain": args.domain,
                "requested_case_ids": sorted(requested_case_ids),
                "business_data_included": False,
            }, ensure_ascii=False, sort_keys=True))
            return 0 if config else 3
        if config is None:
            raise SafeEvaluationError("set BID_ASSESSMENT_MODEL_API_KEY or DEEPSEEK_API_KEY before V603")
        result = asyncio.run(
            _evaluate(
                dataset,
                config,
                domain=args.domain,
                case_ids=requested_case_ids,
            )
        )
        serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output is not None:
            output_path = args.output.resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
        return 0 if result["status"] == "passed" else 2
    except httpx.HTTPError:
        print(json.dumps({
            "status": "blocked",
            "error_type": "ModelTransportUnavailable",
            "message": "model transport is unavailable",
            "business_data_included": False,
        }, ensure_ascii=False, sort_keys=True))
        return 3
    except (OSError, ValueError, SafeEvaluationError) as exc:
        print(json.dumps({
            "status": "blocked",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "business_data_included": False,
        }, ensure_ascii=False, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
