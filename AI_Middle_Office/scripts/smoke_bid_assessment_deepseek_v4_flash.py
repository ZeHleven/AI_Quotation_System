"""One-call synthetic smoke test for the governed DeepSeek V4 Flash adapter.

The script intentionally contains no document, customer, project, or production
data.  It prints only a redacted receipt fingerprint and governed usage fields.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.bid_assessment_local.contracts import TASK_ACTION_ADAPTER
from app.services.bid_mvp1_model_provider import (
    ControlledChatCompletionsProvider,
    DEEPSEEK_PROVIDER_REF,
    DEEPSEEK_V4_FLASH_MODEL_ID,
    DEEPSEEK_V4_FLASH_THINKING_MODE,
)


def main() -> None:
    api_key = (
        os.getenv("BID_ASSESSMENT_MODEL_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
    )
    if not api_key:
        raise RuntimeError("DeepSeek API key is not configured")

    provider = ControlledChatCompletionsProvider(
        session_factory=lambda: None,  # synthetic context bypasses persistence
        provider_ref=DEEPSEEK_PROVIDER_REF,
        model_ref=DEEPSEEK_V4_FLASH_MODEL_ID,
        api_key=api_key,
        chat_url="https://api.deepseek.com/chat/completions",
        thinking_mode=DEEPSEEK_V4_FLASH_THINKING_MODE,
        timeout_seconds=120,
    )
    provider._prompt_context = lambda _envelope: {  # type: ignore[method-assign]
        "run": {
            "run_id": "synthetic-run-1",
            "assessment_id": "synthetic-assessment-1",
            "scope_id": "synthetic-scope-1",
            "evaluation_time": "2026-08-16T00:00:00Z",
        },
        "task": {
            "task_id": "synthetic-task-1",
            "task_type": "validate_report",
            "objective": "Return one finish action because no business input is supplied.",
            "completion_contract": "A schema-valid finish action only.",
        },
        "fact_scope": {
            "assessment_id": "synthetic-assessment-1",
            "lot_id": None,
        },
        "allowed_tools": [],
        "runtime_tools": [],
        "allowed_fact_slots": [],
        "tool_results": [],
        "resolved_facts": [],
        "hard_gates": [],
        "evaluation_time_utc": "2026-08-16T00:00:00Z",
    }
    result = provider.execute(
        request_envelope={
            "provider_ref": DEEPSEEK_PROVIDER_REF,
            "model_ref": DEEPSEEK_V4_FLASH_MODEL_ID,
            "context_manifest_id": "synthetic-context-1",
            "context_manifest_hash": "0" * 64,
            "task_id": "synthetic-task-1",
            "run_id": "synthetic-run-1",
            "action_seq": 1,
            "logical_role": "evidence_validator",
            "input_token_limit": 2_000,
            "output_token_limit": 256,
        },
        provider_request_id="bid-model:phase4b1-smoke:attempt:1",
    )
    try:
        action = TASK_ACTION_ADAPTER.validate_python(result.action)
    except ValidationError as exc:
        print(
            json.dumps(
                {
                    "status": "schema_invalid",
                    "provider_ref": DEEPSEEK_PROVIDER_REF,
                    "model_ref": DEEPSEEK_V4_FLASH_MODEL_ID,
                    "action_type": result.action.get("action_type"),
                    "action_keys": sorted(result.action),
                    "input_tokens": result.usage["input_tokens"],
                    "output_tokens": result.usage["output_tokens"],
                    "actual_cost_microunits": result.actual_cost_microunits,
                    "validation_error_types": sorted(
                        {str(item["type"]) for item in exc.errors()}
                    ),
                    "business_data_included": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(2) from None
    if action.action_type != "finish":
        raise RuntimeError(f"Unexpected smoke action: {action.action_type}")
    receipt_fingerprint = hashlib.sha256(
        result.provider_receipt_id.encode("utf-8")
    ).hexdigest()[:16]
    print(
        json.dumps(
            {
                "status": "ok",
                "provider_ref": DEEPSEEK_PROVIDER_REF,
                "model_ref": DEEPSEEK_V4_FLASH_MODEL_ID,
                "thinking_mode": DEEPSEEK_V4_FLASH_THINKING_MODE,
                "action_type": action.action_type,
                "schema_valid": True,
                "input_tokens": result.usage["input_tokens"],
                "output_tokens": result.usage["output_tokens"],
                "actual_cost_microunits": result.actual_cost_microunits,
                "finish_reason": result.finish_reason,
                "receipt_fingerprint": receipt_fingerprint,
                "business_data_included": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
